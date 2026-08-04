# ponytail: 端到端验证工具路径 + 三类回归。脚本式断言(沿用项目 scripts/test_* 风格)
import httpx, json

BASE = "http://localhost:8000"


def login():
    r = httpx.post(f"{BASE}/api/v1/auth/login", json={"email": "test_front@example.com", "password": "123456"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def stream(question, H, mode="quick"):
    """跑一次 /query/stream(带mode), 收集 source/最终answer/是否出现tool_call thought."""
    resp = httpx.post(f"{BASE}/api/v1/query/stream", json={"question": question, "mode": mode}, headers=H, timeout=120)
    src, answer, saw_tool, saw_retrieve = "", "", False, False
    evt = ""
    for line in resp.text.split("\n"):
        if line.startswith("event:"):
            evt = line[6:].strip()
        elif line.startswith("data:"):
            data = line[5:].strip()
            try:
                obj = json.loads(data)
            except Exception:
                continue
            if evt == "done":
                src = obj.get("source", "")
                answer = obj.get("answer", "")
            elif evt == "thought" and isinstance(obj, dict) and obj.get("tag") == "tool_call":
                saw_tool = True
            elif evt == "step" and isinstance(obj, dict) and obj.get("node") == "retrieve":
                saw_retrieve = True
    return src, answer, saw_tool, saw_retrieve


def main():
    H = login()
    # (问题, 期望source, 期望出现tool_call, 期望不出现retrieve, 标签)
    cases = [
        ("北京今天天气怎么样", "tool", True, True, "工具·天气"),
        ("你好", "non_kb", False, True, "闲聊回归"),
        ("什么是HTTP协议", "fallback_unverified", False, False, "常识→检索兜底(重构后open_qa走检索)"),
        ("计算 123 乘以 456", "tool", True, True, "工具·计算器"),
        ("帮我搜索一下最新的 RAG 综述", "tool", True, True, "工具·搜索"),
    ]
    for q, expect_src, want_tool, no_retrieve, label in cases:
        src, answer, saw_tool, saw_retrieve = stream(q, H)
        ok = (src == expect_src) and (not want_tool or saw_tool) and (not no_retrieve or not saw_retrieve) and bool(answer)
        print(f"[{'PASS' if ok else 'FAIL'}] {label}: q={q!r} source={src} tool_call={saw_tool} retrieve={saw_retrieve}")
        if not ok:
            print(f"        answer={answer[:120]!r}")
        assert ok, f"{label} 失败: source={src}(expect {expect_src}) tool_call={saw_tool} retrieve={saw_retrieve}"
    print("test_tool_calling all pass")


if __name__ == "__main__":
    main()
