# Worker端到端: 直接上传OSS + confirm(触发Celery) → 轮询文档状态流转

import sys, os, time, hashlib, uuid
from dotenv import load_dotenv

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

from fastapi.testclient import TestClient
from apps.api.main import app
from src.storage.oss import OssStore

client = TestClient(app)

r = client.post("/api/v1/auth/login", json={"email": "test_front@example.com", "password": "123456"})
token = r.json()["token"]
H = {"Authorization": f"Bearer {token}"}

# 直接上传PDF到OSS(绕过presign PUT, 聚焦worker消费)
with open("test_doc.pdf", "rb") as f:
    pdf = f.read()
oss = OssStore(endpoint=os.getenv("OSS_ENDPOINT"), bucket=os.getenv("OSS_BUCKET"),
               access_key_id=os.getenv("OSS_ACCESS_KEY_ID"), access_key_secret=os.getenv("OSS_ACCESS_KEY_SECRET"))
oss_key = f"raw/{uuid.uuid4()}/test.pdf"
import asyncio
asyncio.run(oss.put(oss_key, pdf))
print(f"[1] PDF上传OSS: {oss_key}")

# confirm触发Celery
sha = hashlib.sha256(pdf).hexdigest()
r = client.post("/api/v1/documents/confirm", json={"oss_key": oss_key, "title": "test_doc", "file_size": len(pdf), "sha256": sha}, headers=H)
doc_id = r.json()["document_id"]
print(f"[2] confirm触发入库: document_id={doc_id}")

# 轮询状态(PENDING→PARSING→INDEXING→INDEXED)
print("[3] 轮询状态...")
status = None
for i in range(40):
    r = client.get(f"/api/v1/documents/{doc_id}", headers=H)
    status = r.json().get("status")
    if i % 3 == 0 or status in ("INDEXED", "FAILED"):
        print(f"  [{i*2}s] status={status} {r.json().get('error_msg','') or ''}")
    if status in ("INDEXED", "FAILED"):
        break
    time.sleep(2)

ok = status == "INDEXED"
print(f"\n{'✓ Worker入库验证通过(状态流转到INDEXED)' if ok else f'✗ 最终状态={status}'}")
