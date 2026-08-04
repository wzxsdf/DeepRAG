# 端到端验证意图分层路由: 闲聊/能力/库外常识 不白检索(无retrieve节点), KB问题走检索
# 断言: source正确 + 非检索路径的SSE step流无retrieve + KB路径有retrieve
import sys, io, json, httpx
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

BASE = "http://localhost:8000"
r = httpx.post(f"{BASE}/api/v1/auth/login", json={"email": "test_front@example.com", "password": "123456"})
H = {"Authorization": f"Bearer {r.json()['token']}"}


def stream(question):
    """POST /query/stream, 收集 step节点列表 + done.source."""
    nodes, source = [], None
    with httpx.stream("POST", f"{BASE}/api/v1/query/stream", json={"question": question},
                      headers=H, timeout=180) as resp:
        evt = ""
        for line in resp.iter_lines():
            if line.startswith("event:"):
                evt = line[6:].strip()
            elif line.startswith("data:"):
                try:
                    obj = json.loads(line[5:].strip())
                except Exception:
                    continue
                if evt == "step":
                    nodes.append(obj.get("node"))
                elif evt == "done":
                    source = obj.get("source")
    return nodes, source


# (问题, 期望source, 期望是否有retrieve节点)
CASES = [
    ("你好", "non_kb", False),                          # 闲聊: 规则命中, 无retrieve
    ("你能做什么？介绍一下你自己", "non_kb", False),     # 能力: 规则命中, 无retrieve
    ("什么是HTTP协议？请简述", "open_qa", False),        # 库外常识: LLM判open_qa, 无retrieve
    ("知识库里关于检索技术的内容有哪些", None, True),    # KB问题: 走retrieve(source取决于KB内容)
]

ok = True
for q, expect_src, expect_ret in CASES:
    nodes, src = stream(q)
    has_ret = "retrieve" in nodes
    src_ok = expect_src is None or src == expect_src
    ret_ok = has_ret == expect_ret
    flag = "✓" if (src_ok and ret_ok) else "✗"
    if not (src_ok and ret_ok):
        ok = False
    print(f"[{flag}] {q!r:38} source={src!r:20} nodes={nodes}")
    if not src_ok:
        print(f"      期望 source={expect_src!r}")
    if not ret_ok:
        print(f"      期望 retrieve={expect_ret} 实际={has_ret}")

print("\n[OK] 意图分层路由验证通过" if ok else "\n[FAIL] 有用例未通过")
sys.exit(0 if ok else 1)
