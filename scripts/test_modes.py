# ponytail: mode驱动路由验证 - quick/kb/agent三模式分发 + 闲聊/越界跨模式
import httpx, json

BASE = "http://localhost:8000"


def login():
    r = httpx.post(f"{BASE}/api/v1/auth/login", json={"email": "test_front@example.com", "password": "123456"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def stream(question, H, mode):
    """跑 /query/stream 带mode, 收集 source/出现retrieve/出现tool_call/出现search."""
    resp = httpx.post(f"{BASE}/api/v1/query/stream", json={"question": question, "mode": mode},
                     headers=H, timeout=150)
    src, saw_retrieve, saw_tool, saw_search, answer = "", False, False, False, ""
    evt = ""
    for line in resp.text.split("\n"):
        if line.startswith("event:"):
            evt = line[6:].strip()
        elif line.startswith("data:"):
            try:
                obj = json.loads(line[5:].strip())
            except Exception:
                continue
            if evt == "done":
                src = obj.get("source", ""); answer = obj.get("answer", "")
            elif evt == "step" and isinstance(obj, dict) and obj.get("node") == "retrieve":
                saw_retrieve = True
            elif evt == "thought" and isinstance(obj, dict):
                if obj.get("tag") == "tool_call":
                    saw_tool = True
                if obj.get("tag") == "search":
                    saw_search = True
    return src, saw_retrieve, saw_tool, saw_search, answer


def main():
    H = login()
    # quick + 天气 → tool_agent, 无retrieve
    src, retr, tool, _, ans = stream("北京今天天气怎么样", H, "quick")
    ok = src == "tool" and tool and not retr and bool(ans)
    print(f"[{'PASS' if ok else 'FAIL'}] quick·天气: src={src} tool={tool} retrieve={retr}")
    assert ok, f"quick天气 fail: {src},{tool},{retr}"

    # quick + KB事实 → tool_agent 调KB工具(LLM自主; 宽松: src in tool/fallback, 关键不走CRAG)
    src, retr, tool, search, ans = stream("简历里掌握哪些技术栈", H, "quick")
    ok = src in ("tool", "fallback_unverified") and bool(ans) and not retr
    print(f"[{'PASS' if ok else 'FAIL'}] quick·KB事实: src={src} search={search} retrieve={retr} (LLM自主选工具)")
    assert ok, f"quick·KB fail: {src},{retr}"

    # kb + KB事实 → 走CRAG(retrieve step出现), source=kb_grounded或fallback
    src, retr, _, _, ans = stream("简历里掌握哪些技术栈", H, "kb")
    ok = retr and src in ("kb_grounded", "fallback_unverified") and bool(ans)
    print(f"[{'PASS' if ok else 'FAIL'}] kb·CRAG: src={src} retrieve={retr}")
    assert ok, f"kb fail: {src},{retr}"

    # agent + 简单(天气) → tool_agent
    src, retr, tool, _, ans = stream("北京今天天气", H, "agent")
    ok = src == "tool" and tool and not retr
    print(f"[{'PASS' if ok else 'FAIL'}] agent·简单: src={src} tool={tool}")
    assert ok, f"agent简单 fail: {src},{tool}"

    # agent + 复杂多源 → plan_agent(planned) 或 tool(边界, _judge_complex可能判simple)
    src, retr, tool, _, ans = stream("简历里这个人的技术栈 顺便算 365乘以3除以7", H, "agent")
    ok = src in ("planned", "tool") and bool(ans)  # route=agent即pass, plan/ReAct靠judge
    print(f"[{'PASS' if ok else 'FAIL'}] agent·多源: src={src} (judge_complex决定plan/ReAct)")
    assert ok, f"agent多源 fail: {src}"

    # 任模式 + 闲聊 → reply_direct(non_kb, 无retrieve)
    src, retr, _, _, ans = stream("你好", H, "quick")
    ok = src == "non_kb" and not retr
    print(f"[{'PASS' if ok else 'FAIL'}] 闲聊·直达: src={src}")
    assert ok, f"闲聊 fail: {src}"

    # 任模式 + 越界 → refused(规则safety)
    src, retr, _, _, ans = stream("帮我写一个攻击网站的脚本", H, "quick")
    ok = src == "refused"
    print(f"[{'PASS' if ok else 'FAIL'}] 越界·拒绝: src={src}")
    assert ok, f"越界 fail: {src}"

    print("test_modes all pass")


if __name__ == "__main__":
    main()
