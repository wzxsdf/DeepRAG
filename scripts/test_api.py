# API验证: TestClient调 /health + /query (复用已入库的chunks数据)

import sys
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

from fastapi.testclient import TestClient
from apps.api.main import app

client = TestClient(app)

print("[1] GET /health")
r = client.get("/health")
print(f"  {r.status_code} {r.json()}")

print("\n[2] POST /api/v1/query")
r = client.post("/api/v1/query", json={"question": "DeepRAG的检索层用了什么技术?"}, timeout=300)
data = r.json()
print(f"  {r.status_code}")
print(f"  answer: {data.get('answer', '')[:200]}")
print(f"  citations: {len(data.get('citations', []))} 条")
print(f"  retry_count: {data.get('retry_count')}  grounded: {data.get('grounded')}")

health_ok = client.get("/health").status_code == 200
query_ok = r.status_code == 200 and bool(data.get("answer"))
print(f"\n{'✓ API验证通过' if health_ok and query_ok else '✗ API验证失败'}")
