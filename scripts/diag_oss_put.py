# 隔离浏览器PUT 403: Origin / Content-Type 各自是否触发OSS验签失配(httpx baseline已知200)
import httpx
from dotenv import load_dotenv
load_dotenv()
BASE = "http://localhost:8000"
r = httpx.post(f"{BASE}/api/v1/auth/login", json={"email": "test_front@example.com", "password": "123456"})
H = {"Authorization": f"Bearer {r.json()['token']}"}
data = b"%PDF-1.4 diag test content"


def presign(fn):
    return httpx.post(f"{BASE}/api/v1/uploads/presign", json={"filename": fn, "file_size": len(data)}, headers=H).json()["upload_url"]


def t(label, headers):
    rr = httpx.put(presign("d.pdf"), content=data, headers=headers)
    print(f"[{label:18}] {rr.status_code}  {rr.text[:180]}")


t("无header(baseline)", {})
t("+Origin", {"Origin": "http://localhost:5173"})
t("+Content-Type", {"Content-Type": "application/pdf"})
t("+Origin+CT", {"Origin": "http://localhost:5173", "Content-Type": "application/pdf"})
