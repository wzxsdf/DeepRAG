# 后端auth验证: register/login/me/401

import sys
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from fastapi.testclient import TestClient
from apps.api.main import app

email = "test_front@example.com"

# ponytail: with触发lifespan(设app.state.resources), 否则Depends(get_resources)失败
with TestClient(app) as client:
    print("[1] register")
    r = client.post("/api/v1/auth/register", json={"email": email, "password": "123456", "name": "Tester"})
    print(f"  {r.status_code}", r.json().get("token", "")[:20] if r.status_code == 200 else r.json())
    token = r.json().get("token") if r.status_code == 200 else None

    if not token:
        print("[1b] login (已注册过)")
        r = client.post("/api/v1/auth/login", json={"email": email, "password": "123456"})
        print(f"  {r.status_code}")
        token = r.json().get("token")

    print("\n[2] /me 带token")
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    print(f"  {r.status_code} {r.json()}")

    print("\n[3] /me 无token (期望401)")
    r = client.get("/api/v1/auth/me")
    print(f"  {r.status_code} {r.json()}")

    print("\n[4] login 错误密码 (期望401)")
    r = client.post("/api/v1/auth/login", json={"email": email, "password": "wrong"})
    print(f"  {r.status_code} {r.json()}")

    ok = bool(token) and r.status_code == 401
    print(f"\n{'✓ auth验证通过' if ok else '✗ auth异常'}")
