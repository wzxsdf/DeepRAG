# Agent端到端: 完整入库(OSS+MySQL+Qdrant) -> CRAG状态机 -> 带引用答案
# 验证 HybridRetriever.retrieve(OSS拉文本) + retrieve→grade→generate→verify 全链路

import asyncio, os, sys, uuid, hashlib
from dotenv import load_dotenv

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

from src.storage.qdrant_store import QdrantWriter
from src.storage.mysql import MySQLStore
from src.storage.oss import OssStore
from src.retrieval.embedder import embed_query
from src.retrieval.hybrid import HybridRetriever
from src.agents.graph import build_graph

# 知识库: 2条相关 + 1条无关
KB = [
    "DeepRAG是一个Agentic RAG系统,使用LangGraph构建状态机,实现CRAG纠正式检索增强生成。",
    "DeepRAG的检索层采用BGE-M3密集向量与Qdrant原生BM25稀疏向量,通过RRF融合,再用BGE-Reranker精排。",
    "巴黎是法国的首都,以埃菲尔铁塔和卢浮宫闻名于世。",
]
QUESTION = "DeepRAG的检索层用了什么技术?"


async def ingest():
    oss = OssStore(endpoint=os.getenv("OSS_ENDPOINT"), bucket=os.getenv("OSS_BUCKET"),
                   access_key_id=os.getenv("OSS_ACCESS_KEY_ID"), access_key_secret=os.getenv("OSS_ACCESS_KEY_SECRET"))
    mysql = MySQLStore(host=os.getenv("MYSQL_HOST"), port=int(os.getenv("MYSQL_PORT")),
                       db=os.getenv("MYSQL_DATABASE", os.getenv("MYSQL_DB", "deeprag")),
                       user=os.getenv("MYSQL_USER"), password=os.getenv("MYSQL_PASSWORD"))
    writer = QdrantWriter(host=os.getenv("QDRANT_HOST"), port=int(os.getenv("QDRANT_PORT")), api_key=os.getenv("QDRANT_API_KEY", ""))

    writer.recreate_collection()
    doc_id = str(uuid.uuid4())
    ver_id = str(uuid.uuid4())

    await mysql.connect()
    await mysql.execute("INSERT INTO documents (id, title) VALUES (%s, %s)", doc_id, "DeepRAG知识库")
    await mysql.create_document_version(ver_id, doc_id, "raw/test.pdf", 1024, hashlib.sha256(b"test").hexdigest())

    chunks = []
    for i, content in enumerate(KB):
        cid = str(uuid.uuid4())  # 随机id, 避免重跑撞主键
        dense = await embed_query(content)
        oss_key = f"chunks/{ver_id}/{cid}.txt"
        await oss.put(oss_key, content.encode())
        await mysql.insert_chunk(cid, ver_id, ver_id, i + 1, "[]", hashlib.sha256(content.encode()).hexdigest(), oss_key)
        chunks.append({"id": cid, "dense_vector": dense, "sparse_text": content,
                       "payload": {"tenant_id": "default", "kb_id": "default", "document_id": doc_id,
                                   "version_id": ver_id, "parent_id": ver_id, "page": i + 1, "heading_path": [],
                                   "active_version_id": ver_id, "acl_tags": ["default"]}})
    await writer.insert_chunks(chunks)
    await mysql.close()
    print(f"[1] 完整入库 {len(chunks)} 条 (OSS原文 + MySQL记录 + Qdrant向量)")
    return doc_id


async def main():
    print("=" * 60)
    print("DeepRAG Agent CRAG 端到端验证")
    print("=" * 60)
    await ingest()

    print(f"\n[2] 构建CRAG状态机, 提问: {QUESTION}")
    graph = build_graph(HybridRetriever())

    cfg = {"configurable": {"thread_id": "e2e-1"}}
    trace = []
    async for event in graph.astream(
        {"question": QUESTION, "query": QUESTION, "tenant_id": "default", "retry_count": 0,
         "documents": [], "search_decision": "", "generation": "", "verification": None},
        config=cfg, stream_mode="updates",
    ):
        for node, update in event.items():
            trace.append(node)
            print(f"  >> {node}: 更新 {list(update.keys())}")

    values = (await graph.aget_state(config=cfg)).values
    gen = values.get("generation", "")

    print(f"\n[3] 状态机轨迹: {' → '.join(trace)}")
    print(f"    retry_count: {values.get('retry_count', 0)}  |  相关证据: {len(values.get('documents', []))} 条")
    print(f"\n[4] 最终回答:\n{gen}")
    print(f"\n[5] 校验: {values.get('verification')}")

    ok = bool(gen) and "未在知识库" not in gen and any(
        k in gen for k in ("检索", "BGE", "RRF", "rerank", "BM25", "向量"))
    print(f"\n{'✓ Agent端到端验证通过' if ok else '✗ 回答不符合预期'}")
    return ok


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
