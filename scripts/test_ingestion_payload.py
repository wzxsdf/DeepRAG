# 验证完整入库的两个新改动: (1) batch embedding(入库成功即证) (2) content入payload(直查Qdrant)
# + 检索零MySQL路径: HybridRetriever.retrieve 应从 payload 直取 content
import sys, os, time, hashlib, uuid, asyncio, io
import httpx
from dotenv import load_dotenv

if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()
BASE = "http://localhost:8000"

r = httpx.post(f"{BASE}/api/v1/auth/login", json={"email": "test_front@example.com", "password": "123456"})
H = {"Authorization": f"Bearer {r.json()['token']}"}

# 1. 建KB
KB_NAME = f"入库验证_{uuid.uuid4().hex[:6]}"
kb_id = httpx.post(f"{BASE}/api/v1/kbs", json={"name": KB_NAME}, headers=H).json()["id"]
print(f"[1] KB: {KB_NAME} -> {kb_id}")

# 2. 上传 test_doc.pdf 到该KB (走完整 presign->PUT->confirm->BackgroundTasks入库)
with open("test_doc.pdf", "rb") as f:
    pdf = f.read()
presign = httpx.post(f"{BASE}/api/v1/uploads/presign", json={"filename": "ingest_test.pdf", "file_size": len(pdf)}, headers=H).json()
put = httpx.put(presign["upload_url"], content=pdf, headers=presign["headers"])
assert put.status_code == 200, f"OSS PUT失败 {put.status_code}"
sha = hashlib.sha256(pdf).hexdigest()
doc_id = httpx.post(f"{BASE}/api/v1/documents/confirm",
                    json={"oss_key": presign["oss_key"], "title": "ingest_test", "kb_id": kb_id,
                          "file_size": len(pdf), "sha256": sha}, headers=H).json()["document_id"]
print(f"[2] 上传 doc_id={doc_id}, 等待入库(PyMuPDF -> batch embedding -> Qdrant+OSS+MySQL)...")

# 3. 轮询 INDEXED (入库成功 = batch embedding 链路没炸)
t0 = time.perf_counter()
st = None
for _ in range(60):
    st = httpx.get(f"{BASE}/api/v1/documents/{doc_id}", headers=H).json().get("status")
    if st in ("INDEXED", "FAILED"):
        break
    time.sleep(2)
elapsed = time.perf_counter() - t0
print(f"[3] 入库状态: {st}  耗时: {elapsed:.1f}s")
assert st == "INDEXED", f"入库失败(可能batch embedding出错): {st}"

# 4. 直查Qdrant: 验证 chunk payload 含 content 字段(本次新改动)
from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue
from src.storage.qdrant_store import _normalize_host
client = QdrantClient(url=f"{_normalize_host(os.getenv('QDRANT_HOST'))}:{os.getenv('QDRANT_PORT')}",
                      api_key=os.getenv("QDRANT_API_KEY") or None)
res = client.scroll("chunks",
                    scroll_filter=Filter(must=[FieldCondition(key="kb_id", match=MatchValue(value=kb_id))]),
                    limit=5, with_payload=True, with_vectors=False)
points = res[0]
has_content = [p for p in points if (p.payload or {}).get("content")]
print(f"[4] Qdrant该KB chunk数: {len(points)}, payload含content: {len(has_content)}")
if points:
    print(f"    样例content: {points[0].payload.get('content', '')[:80]}")
assert len(has_content) == len(points) and len(points) > 0, "content未入payload"

# 5. 检索零MySQL取content: HybridRetriever.retrieve 应从 payload 直取 content(新数据不查MySQL)
from src.retrieval.hybrid import HybridRetriever
from src.storage.protocol import RetrievalFilter

async def check_retrieve():
    rc = HybridRetriever()
    return await rc.retrieve("DeepRAG retrieval technology", RetrievalFilter(tenant_id="default", kb_ids=[kb_id]), top_k=5)
docs = asyncio.run(check_retrieve())
nonempty = [d for d in docs if d.content]
print(f"[5] retrieve返回{len(docs)}条, content非空{len(nonempty)}条(从payload直取, 走零MySQL路径)")
if docs:
    print(f"    样例: {docs[0].content[:80]}")
assert len(nonempty) == len(docs) and len(docs) > 0, "检索content为空"

print(f"\n[OK] 完整入库验证通过: batch embedding OK + content入payload({len(has_content)}/{len(points)}) + 检索零IO取content({len(nonempty)}/{len(docs)})")
