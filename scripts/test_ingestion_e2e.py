# 入库端到端验证: PyMuPDF解析 → SiliconFlow向量化 → Qdrant+MySQL+OSS → 检索命中

import asyncio, os, sys, uuid, json
from dotenv import load_dotenv

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

from src.storage.oss import OssStore
from src.storage.qdrant_store import QdrantWriter
from src.retrieval.embedder import embed_query
from apps.worker.ingestion_core import parse_document_from_oss, index_chunks_to_qdrant

PDF_PATH = "test_doc.pdf"


async def main():
    oss = OssStore(endpoint=os.getenv("OSS_ENDPOINT"), bucket=os.getenv("OSS_BUCKET"),
                   access_key_id=os.getenv("OSS_ACCESS_KEY_ID"), access_key_secret=os.getenv("OSS_ACCESS_KEY_SECRET"))
    version_id = str(uuid.uuid4())

    # 1. 上传PDF到OSS
    with open(PDF_PATH, "rb") as f:
        pdf_data = f.read()
    oss_key = f"raw/{version_id}/test.pdf"
    await oss.put(oss_key, pdf_data)
    print(f"[1] PDF上传OSS: {len(pdf_data)} bytes → {oss_key}")

    # 1.5 创建 document+version(模拟confirm, 满足 chunks 外键)
    import hashlib
    from src.storage.mysql import MySQLStore
    mysql = MySQLStore(host=os.getenv("MYSQL_HOST"), port=int(os.getenv("MYSQL_PORT")),
                       db=os.getenv("MYSQL_DATABASE", os.getenv("MYSQL_DB", "deeprag")),
                       user=os.getenv("MYSQL_USER"), password=os.getenv("MYSQL_PASSWORD"))
    doc_id = str(uuid.uuid4())
    await mysql.connect()
    await mysql.execute("INSERT INTO documents (id, title) VALUES (%s, %s)", doc_id, "test_doc")
    await mysql.create_document_version(version_id, doc_id, oss_key, len(pdf_data), hashlib.sha256(pdf_data).hexdigest())
    await mysql.close()

    # 2. PyMuPDF解析
    parsed = await parse_document_from_oss(version_id, oss_key)
    parsed_data = await oss.get(parsed["parsed_key"])
    texts = json.loads(parsed_data.decode())["texts"]
    print(f"[2] PyMuPDF解析: {len(texts)} 文本块")
    for t in texts[:2]:
        print(f"    page={t['page']} {t['text'][:60]}")

    # 3. 切分+向量化+入库
    result = await index_chunks_to_qdrant(version_id, parsed["parsed_key"])
    print(f"[3] 索引完成: {result['indexed_count']} chunks → Qdrant+MySQL+OSS")

    # 4. 检索验证
    q = QdrantWriter(host=os.getenv("QDRANT_HOST"), port=int(os.getenv("QDRANT_PORT")), api_key=os.getenv("QDRANT_API_KEY", ""))
    dense = await embed_query("DeepRAG used what retrieval technology?")
    fused = q.query_hybrid(dense, "DeepRAG retrieval", tenant_id="default", limit=3)
    print(f"[4] 检索命中 {len(fused)} 条")
    for p in fused:
        print(f"    score={p.score:.3f}")

    ok = len(fused) > 0
    print(f"\n{'✓ 入库端到端验证通过(PyMuPDF+SiliconFlow+Qdrant)' if ok else '✗ 检索无结果'}")
    return ok


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
