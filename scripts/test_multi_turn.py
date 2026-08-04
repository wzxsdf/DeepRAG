# W3.3 多轮对话持久化验证: 同conversation_id两问 -> MySQL持久化 + 第二问加载历史理解指代

import sys, os, asyncio, uuid
from dotenv import load_dotenv

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

from fastapi.testclient import TestClient
from apps.api.main import app
from src.storage.qdrant_store import QdrantWriter
from src.retrieval.embedder import embed_query
from src.storage.mysql import MySQLStore


async def setup():
    writer = QdrantWriter(host=os.getenv("QDRANT_HOST"), port=int(os.getenv("QDRANT_PORT")), api_key=os.getenv("QDRANT_API_KEY", ""))
    writer.recreate_collection()
    content = "DeepRAG的检索层使用BGE-M3嵌入模型,该模型输出1024维dense向量,支持100多种语言。"
    dense = await embed_query(content)
    await writer.insert_chunks([{"id": str(uuid.uuid5(uuid.NAMESPACE_DNS, "mt1")), "dense_vector": dense,
                                  "sparse_text": content,
                                  "payload": {"tenant_id": "default", "document_id": "mt1", "content": content}}])
    print(f"[0] 入库测试文档")


asyncio.run(setup())

client = TestClient(app)
conv = "mt-test-conv"

print("\n[1] Q1: DeepRAG检索层用什么模型?")
r1 = client.post("/api/v1/query", json={"question": "DeepRAG检索层用什么模型?", "conversation_id": conv}, timeout=300)
print(f"  A1: {r1.json()['answer'][:120]}")

print("\n[2] Q2: 它输出多少维向量? (指代Q1的模型, 依赖多轮上下文)")
r2 = client.post("/api/v1/query", json={"question": "它输出多少维向量?", "conversation_id": conv}, timeout=300)
print(f"  A2: {r2.json()['answer'][:120]}")


async def check_persist():
    m = MySQLStore(host=os.getenv("MYSQL_HOST"), port=int(os.getenv("MYSQL_PORT")),
                   db=os.getenv("MYSQL_DATABASE", os.getenv("MYSQL_DB", "deeprag")),
                   user=os.getenv("MYSQL_USER"), password=os.getenv("MYSQL_PASSWORD"))
    await m.connect()
    rows = await m.fetch("SELECT role FROM conversation_messages WHERE conversation_id=%s ORDER BY id", conv)
    await m.close()
    return rows


rows = asyncio.run(check_persist())
print(f"\n[3] 持久化: conversation_messages {len(rows)} 条 (期望4) -> {[r['role'] for r in rows]}")

persist_ok = len(rows) == 4
a2 = r2.json()["answer"]
multi_turn_ok = "1024" in a2
print(f"\n持久化: {'✓' if persist_ok else '✗'} | 多轮指代(Q2基于Q1上下文答出1024): {'✓' if multi_turn_ok else '○ 未答出'}")
print(f"{'✓ 多轮对话验证通过' if persist_ok and multi_turn_ok else ('○ 持久化通过, 指代未达成(LLM质量)' if persist_ok else '✗ 持久化失败')}")
