# ponytail: 入库核心逻辑(不依赖Celery),供测试和任务调用

import os
import uuid
import hashlib
import asyncio
from typing import List, Dict
from dotenv import load_dotenv
import json

# 加载环境变量
load_dotenv()

# ponytail: 使用SiliconFlow embedding API
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3")
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "")
EMBEDDING_BASE_URL = os.getenv("EMBEDDING_BASE_URL", "")
EMBEDDING_DIMENSIONS = int(os.getenv("EMBEDDING_DIMENSIONS", "1024"))

async def get_embeddings_siliconflow(texts: List[str]) -> List[List[float]]:
    """通过SiliconFlow API批量获取embedding(input传数组, 32条/批)。原逐条N次HTTP→N/32批。"""
    import httpx

    api_url = f"{EMBEDDING_BASE_URL}/embeddings"
    headers = {"Authorization": f"Bearer {EMBEDDING_API_KEY}", "Content-Type": "application/json"}
    out: List[List[float]] = [None] * len(texts)  # ponytail: 预分配按 index 回填(批内index 0-based)
    BS = 32
    async with httpx.AsyncClient(timeout=60) as client:
        for i in range(0, len(texts), BS):
            batch = texts[i:i + BS]
            payload = {"model": EMBEDDING_MODEL, "input": batch, "encoding_format": "float"}
            response = await client.post(api_url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json().get("data", [])
            if not data:
                raise ValueError(f"Unexpected API response (empty data) at batch {i}")
            for d in data:
                out[i + d.get("index", 0)] = d["embedding"]
    if any(e is None for e in out):
        raise ValueError("embedding 部分缺失")
    return out

async def embed_chunks(chunks: List[Dict]) -> List[Dict]:
    """API向量化"""
    contents = [c["content"] for c in chunks]

    # 调用SiliconFlow embedding
    embeddings = await get_embeddings_siliconflow(contents)

    for i, chunk in enumerate(chunks):
        chunk["dense_vector"] = embeddings[i]
        # ponytail: sparse走Qdrant原生BM25, 用原文(字符级匹配, W3.1实验证明比jieba分词更精确)
        chunk["sparse_text"] = chunk["content"]
        chunk["content_hash"] = hashlib.sha256(chunk["content"].encode()).hexdigest()

    return chunks

def split_parent_child(parsed_content: str) -> List[Dict]:
    """Parent-Child切分. ponytail: parent_content为整组完整文本(评审修复20261126 —
    旧实现各child存累加过程快照, 组内第2+个child命中所见"父块"只有第1段, 父块展开名存实亡)."""
    doc_data = json.loads(parsed_content)
    chunks = []
    current_parent = ""
    parent_id = str(uuid.uuid4())
    child_offset = 0

    for text in doc_data.get("texts", []):
        content = text.get("text", "")
        if not content.strip():
            continue

        current_parent += content + "\n\n"
        child_id = str(uuid.uuid4())  # ponytail: Qdrant point id须UUID(非uuid-offset)
        child_offset += 1

        chunks.append({
            "id": child_id,
            "parent_id": parent_id,
            "content": content.strip(),
            "parent_content": current_parent.strip(),
            "locator": {
                "page": text.get("page", 0),
            },
        })

        if len(current_parent) > 4000:
            current_parent = ""
            parent_id = str(uuid.uuid4())

    # 修正: 每组最后一个child的parent_content即完整组文本(累加到最后), 回填给组内全部child
    full_by_parent: Dict[str, str] = {}
    for c in chunks:
        pid = c.get("parent_id")
        if pid and c.get("parent_content"):
            full_by_parent[pid] = c["parent_content"]
    for c in chunks:
        pid = c.get("parent_id")
        if pid in full_by_parent:
            c["parent_content"] = full_by_parent[pid]

    return chunks

def _extract_pdf_texts(pdf_data: bytes) -> List[Dict]:
    """PyMuPDF按页提取文本块(同步CPU密集, 单独抽出便于to_thread包裹)."""
    import fitz
    doc = fitz.open(stream=pdf_data, filetype="pdf")
    texts = []
    for i, page in enumerate(doc):
        for b in page.get_text("blocks"):
            text = b[4].strip() if len(b) > 4 else ""
            if text:
                texts.append({"text": text, "page": i + 1})
    doc.close()
    return texts


async def parse_document_from_oss(version_id: str, oss_key: str) -> Dict:
    """从OSS解析文档(PyMuPDF, 保留页码+段落块). Docling因Win中文路径编码bug弃用.
    ponytail: fitz解析是同步CPU密集操作, to_thread包裹避免阻塞API事件循环(评审修复20261126)."""
    from src.storage.oss import OssStore

    oss_store = OssStore(
        endpoint=os.getenv("OSS_ENDPOINT"),
        bucket=os.getenv("OSS_BUCKET"),
        access_key_id=os.getenv("OSS_ACCESS_KEY_ID"),
        access_key_secret=os.getenv("OSS_ACCESS_KEY_SECRET"),
    )
    pdf_data = await oss_store.get(oss_key)
    texts = await asyncio.to_thread(_extract_pdf_texts, pdf_data)

    parsed_content = json.dumps({"texts": texts}, ensure_ascii=False)
    parsed_key = f"parsed/{version_id}/document.json"
    await oss_store.put(parsed_key, parsed_content.encode())
    return {"version_id": version_id, "status": "parsed", "parsed_key": parsed_key}

async def index_chunks_to_qdrant(version_id: str, parsed_key: str, kb_id: str = "default", document_id: str = "") -> Dict:
    """切分+向量化+写Qdrant+写PG. kb_id/document_id写入chunk payload供多KB检索过滤+按文档删除."""
    from src.storage.oss import OssStore
    from src.storage.qdrant_store import QdrantWriter
    from src.storage.mysql import MySQLStore
    from src.storage.repositories import ChunkRepository

    # 读解析产物
    oss_store = OssStore(
        endpoint=os.getenv("OSS_ENDPOINT"),
        bucket=os.getenv("OSS_BUCKET"),
        access_key_id=os.getenv("OSS_ACCESS_KEY_ID"),
        access_key_secret=os.getenv("OSS_ACCESS_KEY_SECRET"),
    )
    parsed_data = await oss_store.get(parsed_key)
    parsed_content = parsed_data.decode()

    # 切分
    chunks_data = split_parent_child(parsed_content)

    # 向量化
    chunks_with_vectors = await embed_chunks(chunks_data)

    # 写Qdrant
    qdrant = QdrantWriter(
        host=os.getenv("QDRANT_HOST"),
        port=int(os.getenv("QDRANT_PORT")),
    )
    qdrant.ensure_collection()

    points = []
    for chunk in chunks_with_vectors:
        points.append({
            "id": chunk["id"],
            "dense_vector": chunk["dense_vector"],
            "sparse_text": chunk["sparse_text"],
            "payload": {
                "tenant_id": "default",
                "kb_id": kb_id,
                "document_id": document_id or version_id,  # ponytail: 真document_id(修原误写version_id的bug); 兜底version_id兼容旧调用
                "version_id": version_id,
                "parent_id": chunk["parent_id"],
                "page": chunk["locator"]["page"],
                "heading_path": [],
                "active_version_id": version_id,
                "acl_tags": ["default"],
                "content": chunk["content"],  # ponytail: child原文入payload, 检索零IO直取(省OSS回拉; parent过大仍留OSS)
            },
        })

    await qdrant.insert_chunks(points)

    # 写MySQL chunks表
    mysql_store = MySQLStore(
        host=os.getenv("MYSQL_HOST"),
        port=int(os.getenv("MYSQL_PORT")),
        db=os.getenv("MYSQL_DATABASE", os.getenv("MYSQL_DB", "deeprag")),
        user=os.getenv("MYSQL_USER"),
        password=os.getenv("MYSQL_PASSWORD"),
    )
    await mysql_store.connect()
    chunk_repo = ChunkRepository(mysql_store)

    # ponytail: W3.2 父块单独落OSS(per parent_id), 供检索parent expansion
    seen_parents = set()
    for chunk in chunks_with_vectors:
        pid = chunk.get("parent_id")
        if pid and pid not in seen_parents and chunk.get("parent_content"):
            await oss_store.put(f"parent_chunks/{pid}.txt", chunk["parent_content"].encode())
            seen_parents.add(pid)

    for chunk in chunks_with_vectors:
        chunk_oss_key = f"chunks/{version_id}/{chunk['id']}.txt"
        await oss_store.put(chunk_oss_key, chunk["content"].encode())

        # MySQL没有数组,heading_path转为JSON字符串
        import json
        heading_path_str = json.dumps([])

        await chunk_repo.insert(
            chunk_id=chunk["id"],
            version_id=version_id,
            parent_id=chunk["parent_id"],
            page=chunk["locator"]["page"],
            heading_path=heading_path_str,
            content_hash=chunk["content_hash"],
            oss_key=chunk_oss_key,
        )

    await mysql_store.close()

    return {
        "version_id": version_id,
        "indexed_count": len(chunks_with_vectors),
        "status": "indexed",
    }
