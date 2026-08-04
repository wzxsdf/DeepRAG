# 后端补全验证: metrics/documents/conversations/stream(带token) + 401

import sys
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from fastapi.testclient import TestClient
from apps.api.main import app

client = TestClient(app)
email = "test_front@example.com"

r = client.post("/api/v1/auth/login", json={"email": email, "password": "123456"})
token = r.json().get("token") if r.status_code == 200 else None
if not token:
    token = client.post("/api/v1/auth/register", json={"email": email, "password": "123456", "name": "T"}).json()["token"]
H = {"Authorization": f"Bearer {token}"}
print(f"[0] token ok\n")

r = client.get("/api/v1/metrics", headers=H)
print(f"[1] /metrics {r.status_code}", r.json() if r.status_code == 200 else r.text[:80])

r = client.get("/api/v1/documents", headers=H)
print(f"[2] /documents {r.status_code} 文档数={len(r.json()) if r.status_code == 200 else 'err'}")

r = client.get("/api/v1/conversations", headers=H)
print(f"[3] /conversations {r.status_code} 会话数={len(r.json()) if r.status_code == 200 else 'err'}")

r = client.get("/api/v1/metrics")
print(f"[4] /metrics 无token {r.status_code} (期望401)")

print("\n[5] /query/stream (SSE)...")
events = []
with client.stream("POST", "/api/v1/query/stream", json={"question": "你好"}, headers=H, timeout=300) as resp:
    for line in resp.iter_lines():
        if line:
            events.append(line)
event_types = [l for l in events if l.startswith("event:")]
print(f"  收到事件: {event_types}")
done_lines = [l for l in events if l.startswith("data:") and '"answer"' in l]
print(f"  done含answer: {'是' if done_lines else '否'}")

ok = (r.status_code == 401 if False else True)  # [4] 检查在下面
metrics_ok = client.get("/api/v1/metrics", headers=H).status_code == 200
no_token_ok = client.get("/api/v1/metrics").status_code == 401
stream_ok = any("event: done" in e for e in event_types)
print(f"\nmetrics✓={metrics_ok} 401✓={no_token_ok} stream✓={stream_ok}")
print(f"{'✓ 后端补全验证通过' if metrics_ok and no_token_ok and stream_ok else '✗ 有失败项'}")
