# 端到端验证KB生命周期: 建→传2文档→删1文档(向量清理+KB保留)→删KB(全清)→幂等→检索隔离
import sys, os, io, time, hashlib, uuid, json, httpx
from dotenv import load_dotenv
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()
BASE = "http://localhost:8000"

r = httpx.post(f"{BASE}/api/v1/auth/login", json={"email": "test_front@example.com", "password": "123456"})
H = {"Authorization": f"Bearer {r.json()['token']}"}

from qdrant_client import QdrantClient
from qdrant_client.models import Filter, FieldCondition, MatchValue
from src.storage.qdrant_store import _normalize_host
qclient = QdrantClient(url=f"{_normalize_host(os.getenv('QDRANT_HOST'))}:{os.getenv('QDRANT_PORT')}",
                       api_key=os.getenv("QDRANT_API_KEY") or None)


def qcount(**match):
    must = [FieldCondition(key=k, match=MatchValue(value=v)) for k, v in match.items()]
    return qclient.count("chunks", count_filter=Filter(must=must)).count


def upload_doc(kb_id, fname):
    with open("test_doc.pdf", "rb") as f:
        pdf = f.read()
    presign = httpx.post(f"{BASE}/api/v1/uploads/presign", json={"filename": fname, "file_size": len(pdf)}, headers=H).json()
    assert httpx.put(presign["upload_url"], content=pdf, headers=presign["headers"]).status_code == 200
    sha = hashlib.sha256(pdf).hexdigest()
    return httpx.post(f"{BASE}/api/v1/documents/confirm",
                      json={"oss_key": presign["oss_key"], "title": fname, "kb_id": kb_id,
                            "file_size": len(pdf), "sha256": sha}, headers=H).json()["document_id"]


def wait_indexed(doc_id):
    for _ in range(60):
        st = httpx.get(f"{BASE}/api/v1/documents/{doc_id}", headers=H).json().get("status")
        if st in ("INDEXED", "FAILED"):
            return st
        time.sleep(2)
    return "TIMEOUT"


# 1. 建KB
kb_id = httpx.post(f"{BASE}/api/v1/kbs", json={"name": f"生命周期_{uuid.uuid4().hex[:6]}"}, headers=H).json()["id"]
print(f"[1] KB: {kb_id}")

# 2. 传2文档入库
d1 = upload_doc(kb_id, "life_doc1.pdf")
d2 = upload_doc(kb_id, "life_doc2.pdf")
print(f"[2] 上传 d1={d1[:8]} d2={d2[:8]}, 等入库...")
assert wait_indexed(d1) == "INDEXED" and wait_indexed(d2) == "INDEXED", "入库失败"

v1 = httpx.get(f"{BASE}/api/v1/documents/{d1}", headers=H).json()["version_id"]
n_kb, n_v1 = qcount(kb_id=kb_id), qcount(version_id=v1)
print(f"[3] 入库后 Qdrant: kb={n_kb}条, doc1(version)={n_v1}条")
assert n_kb > 0 and n_v1 > 0, "向量未写入"

# 3. 删文档1 → 验证向量清理 + KB保留
del1 = httpx.delete(f"{BASE}/api/v1/documents/{d1}", headers=H)
print(f"[4] 删文档1: {del1.status_code} {del1.json()}")
assert del1.status_code == 200
docs = httpx.get(f"{BASE}/api/v1/documents", params={"kb_id": kb_id}, headers=H).json()
ids = [d["id"] for d in docs]
assert d1 not in ids and d2 in ids, f"文档列表过滤异常: {ids}"
assert qcount(version_id=v1) == 0, "文档1向量未清"
assert qcount(kb_id=kb_id) == n_kb - n_v1, "KB向量数应减doc1的量"
cur = [k for k in httpx.get(f"{BASE}/api/v1/kbs", headers=H).json() if k["id"] == kb_id][0]
assert cur["doc_count"] == 1, f"KB doc_count应=1, 实际{cur['doc_count']}"
print(f"[5] ✓ 删文档1后: doc1向量=0, KB向量={qcount(kb_id=kb_id)}, doc_count=1, 列表只剩doc2")

# 4. 删KB → 验证全清
del_kb = httpx.delete(f"{BASE}/api/v1/kbs/{kb_id}", headers=H)
print(f"[6] 删KB: {del_kb.status_code} {del_kb.json()}")
assert del_kb.status_code == 200
assert qcount(kb_id=kb_id) == 0, "KB向量未清空"
assert kb_id not in [k["id"] for k in httpx.get(f"{BASE}/api/v1/kbs", headers=H).json()], "KB未从列表过滤"

# 5. 幂等: 再删已删KB
del_again = httpx.delete(f"{BASE}/api/v1/kbs/{kb_id}", headers=H)
print(f"[7] 幂等再删KB: {del_again.status_code}")
assert del_again.status_code == 200

# 6. 检索隔离: 查该KB内容应无citation(向量已删)
cites = -1
with httpx.stream("POST", f"{BASE}/api/v1/query/stream", json={"question": "检索技术", "kb_ids": [kb_id]},
                  headers=H, timeout=180) as resp:
    evt = ""
    for line in resp.iter_lines():
        if line.startswith("event:"):
            evt = line[6:].strip()
        elif line.startswith("data:") and evt == "done":
            try:
                cites = len(json.loads(line[5:].strip()).get("citations", []))
            except Exception:
                pass
print(f"[8] 删KB后检索该KB: citations={cites}")
assert cites == 0, "删KB后仍检索到citation(向量未隔离)"

print("\n[OK] KB生命周期验证通过: 删文档(向量清理+KB保留) + 删KB(全清) + 幂等 + 检索隔离")
