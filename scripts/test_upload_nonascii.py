# 回归测试: 中文+全角文件名上传(曾因presigned URL非ASCII编码失配导致NoSuchKey)
# 走真实API: presign(应返ASCII key) → PUT OSS → confirm → 轮询入库状态

import os, sys, time, hashlib
import httpx
from dotenv import load_dotenv

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

BASE = "http://localhost:8000"
FILENAME = "魏子轩—19892285708.pdf"  # 中文 + 全角破折号(em-dash U+2014)

# login
r = httpx.post(f"{BASE}/api/v1/auth/login", json={"email": "test_front@example.com", "password": "123456"})
token = r.json()["token"]
H = {"Authorization": f"Bearer {token}"}

with open("test_doc.pdf", "rb") as f:
    pdf = f.read()

# 1. presign — oss_key 必须是 ASCII
r = httpx.post(f"{BASE}/api/v1/uploads/presign", json={"filename": FILENAME, "file_size": len(pdf), "content_type": "application/pdf"}, headers=H)
presign = r.json()
oss_key = presign["oss_key"]
print(f"[1] presign oss_key = {oss_key!r}")
assert oss_key.isascii(), f"oss_key 必须ASCII! got {oss_key!r}"
print(f"    ASCII key ✓")

# 2. PUT OSS(模拟浏览器)
resp = httpx.put(presign["upload_url"], content=pdf, headers=presign["headers"])
print(f"[2] PUT status = {resp.status_code}")
assert resp.status_code == 200, f"PUT失败: {resp.status_code} {resp.text[:200]}"

# 3. confirm
sha = hashlib.sha256(pdf).hexdigest()
r = httpx.post(f"{BASE}/api/v1/documents/confirm", json={"oss_key": oss_key, "title": FILENAME, "file_size": len(pdf), "sha256": sha}, headers=H)
doc_id = r.json()["document_id"]
print(f"[3] confirm doc_id = {doc_id}")

# 4. 轮询状态
status = None
for i in range(40):
    r = httpx.get(f"{BASE}/api/v1/documents/{doc_id}", headers=H)
    status = r.json().get("status")
    if i % 3 == 0 or status in ("INDEXED", "FAILED"):
        print(f"  [{i*2}s] status={status} {r.json().get('error_msg','') or ''}")
    if status in ("INDEXED", "FAILED"):
        break
    time.sleep(2)

ok = status == "INDEXED"
print(f"\n{'✓ 非ASCII文件名上传入库通过' if ok else f'✗ 失败 status={status}'}")
sys.exit(0 if ok else 1)
