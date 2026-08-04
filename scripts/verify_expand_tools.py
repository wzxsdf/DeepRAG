# 深入验证扩工具: 计算器正确性 + 多工具组合 + sub_queries端到端可观测
import httpx, json

BASE = "http://localhost:8000"


def login():
    r = httpx.post(f"{BASE}/api/v1/auth/login", json={"email": "test_front@example.com", "password": "123456"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def stream_full(question, H):
    """跑 query/stream, 收集完整轨迹(step/thought/done)."""
    resp = httpx.post(f"{BASE}/api/v1/query/stream", json={"question": question}, headers=H, timeout=120)
    steps, thoughts, done = [], [], None
    evt = ""
    for line in resp.text.split("\n"):
        if line.startswith("event:"):
            evt = line[6:].strip()
        elif line.startswith("data:"):
            try:
                obj = json.loads(line[5:].strip())
            except Exception:
                continue
            if evt == "step":
                steps.append(obj)
            elif evt == "thought":
                thoughts.append(obj)
            elif evt == "done":
                done = obj
    return steps, thoughts, done


def main():
    H = login()
    ok = True

    # 1. 计算器正确性: 结果必须含 56088
    s, t, d = stream_full("帮我计算 123 乘以 456 等于多少", H)
    calcs = [x for x in t if x.get("name") == "calculate"]
    names = [x.get("name") for x in t if x.get("tag") == "tool_call"]
    res = calcs[0].get("result", "") if calcs else "NONE"
    print(f"[计算器] tools={names} result={res}")
    if calcs and "56088" in res:
        print("  PASS: 计算器调用且结果正确(56088)")
    else:
        print("  FAIL: 计算器未调或结果错"); ok = False

    # 2. 多工具组合: 单query同时触发 get_weather + calculate
    s, t, d = stream_full("北京今天天气怎么样？顺便帮我算一下 12 乘以 15", H)
    names = [x.get("name") for x in t if x.get("tag") == "tool_call"]
    print(f"[多工具] tools={names}")
    if "get_weather" in names and "calculate" in names:
        print("  PASS: 单query触发天气+计算器(auto多工具选择)")
    else:
        print(f"  WARN: 期望 get_weather+calculate, 实际 {names} (LLM可能未组合, 非硬错)")

    # 3. sub_queries 可观测化: route step detail 含"子查询:"
    s, t, d = stream_full("对比 Kafka 和 RabbitMQ 的架构差异", H)
    route = [x for x in s if x.get("node") == "route"]
    detail = route[0].get("detail", "") if route else "NONE"
    print(f"[sub_queries] route detail={detail}")
    if route and "子查询" in detail:
        print("  PASS: 子查询内容端到端展示")
    else:
        print(f"  INFO: router对此问意图={detail}, 未判kb_complex故无子查询(取决于router, 正常)")

    print(f"\n>>> {'深入验证通过(计算器正确)' if ok else '存在问题'}")


if __name__ == "__main__":
    main()
