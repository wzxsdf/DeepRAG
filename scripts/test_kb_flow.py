# KB全链路: 创建KB → 列表 → 上传入指定KB → 轮询INDEXED → chunk payload含正确kb_id → 多KB检索过滤
# 走真实8000端口 + 直接查Qdrant验证payload

import sys, os, time, hashlib, uuid, asyncio
import httpx
from dotenv import load_dotenv

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()
BASE = "http://localhost:8000"

r = httpx.post(f"{BASE}/api/v1/auth/login", json={"email": "test_front@example.com", "password": "123456"})
H = {"Authorization": f"Bearer {r.json()['token']}"}

# 1. 创建KB
KB_NAME = f"测试KB_{uuid.uuid4().hex[:6]}"
r = httpx.post(f"{BASE}/api/v1/kbs", json={"name": KB_NAME}, headers=H)
kb = r.json()
kb_id = kb["id"]
print(f"[1] 创建KB: {KB_NAME} → {kb_id}")

# 2. 列表
r = httpx.get(f"{BASE}/api/v1/kbs", headers=H)
kbs = r.json()
print(f"[2] KB列表({len(kbs)}个), 新KB在其中: {any(k['id'] == kb_id for k in kbs)}")

# 3. 上传test_doc到该KB
with open("test_doc.pdf", "rb") as f:
    pdf = f.read()
presign = httpx.post(f"{BASE}/api/v1/uploads/presign", json={"filename": "kb_test.pdf", "file_size": len(pdf)}, headers=H).json()
assert httpx.put(presign["upload_url"], content=pdf, headers=presign["headers"]).status_code == 200
sha = hashlib.sha256(pdf).hexdigest()
conf = httpx.post(f"{BASE}/api/v1/documents/confirm",
                  json={"oss_key": presign["oss_key"], "title": "kb_test", "kb_id": kb_id, "file_size": len(pdf), "sha256": sha}, headers=H).json()
doc_id = conf["document_id"]
print(f"[3] 上传到KB, doc_id={doc_id}")

# 4. 轮询INDEXED
for i in range(40):
    st = httpx.get(f"{BASE}/api/v1/documents/{doc_id}", headers=H).json().get("status")
    if st in ("INDEXED", "FAILED"):
        print(f"[4] 入库状态: {st}"); break
    time.sleep(2)
assert st == "INDEXED", f"入库失败: {st}"

# 5. 验证chunk payload的kb_id正确(直接查Qdrant)
from src.storage.qdrant_store import QdrantWriter
q = QdrantWriter(host=os.getenv("QDRANT_HOST"), port=int(os.getenv("QDRANT_PORT")), api_key=os.getenv("QDRANT_API_KEY", ""))
# 用query_hybrid分别按 本KB / 不存在KB 过滤, 看命中数
dense_url = os.getenv("EMBEDDING_BASE_URL") + "/embeddings"
emb = httpx.post(dense_url, headers={"Authorization": f"Bearer {os.getenv('EMBEDDING_API_KEY')}"},
                 json={"model": "BAAI/bge-m3", "input": ["DeepRAG retrieval technology"], "encoding_format": "float"}).json()["data"][0]["embedding"]

hit_right = q.query_hybrid(dense_vec=emb, query_text="DeepRAG retrieval", tenant_id="default", kb_ids=[kb_id], limit=5)
hit_wrong = q.query_hybrid(dense_vec=emb, query_text="DeepRAG retrieval", tenant_id="default", kb_ids=[uuid.uuid4().hex], limit=5)
print(f"[5] 按本KB过滤命中: {len(hit_right)} 条; 按不存在KB过滤命中: {len(hit_wrong)} 条")

# 6. 预览URL
prev = httpx.get(f"{BASE}/api/v1/documents/{doc_id}/preview", headers=H).json()
print(f"[6] 预览URL: {prev['url'][:80]}...")
prev_ok = "aliyuncs.com" in prev["url"]

ok = st == "INDEXED" and len(hit_right) > 0 and len(hit_wrong) == 0 and prev_ok
print(f"\n{'✓ KB全链路验证通过(创建/上传归属/payload kb_id/多KB过滤/预览)' if ok else '✗ 验证未通过'}")
sys.exit(0 if ok else 1)
