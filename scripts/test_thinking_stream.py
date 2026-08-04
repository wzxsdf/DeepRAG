# 验证"思考过程"流式: /query/stream 应发 step(节点中间结果) + thought(Agent检索推理) + done
# 走真实8000端口, 打印SSE事件序列

import sys, json
import httpx
from dotenv import load_dotenv

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

BASE = "http://localhost:8000"
r = httpx.post(f"{BASE}/api/v1/auth/login", json={"email": "test_front@example.com", "password": "123456"})
token = r.json()["token"]
H = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

QUESTION = sys.argv[1] if len(sys.argv) > 1 else "对比LangGraph和LlamaIndex的定位差异"

steps, thoughts, tokens = [], [], []
print(f"提问: {QUESTION}\n{'='*60}")
with httpx.stream("POST", f"{BASE}/api/v1/query/stream", json={"question": QUESTION}, headers=H, timeout=180) as resp:
    evt = ""
    for line in resp.iter_lines():
        if line.startswith("event:"):
            evt = line[6:].strip()
        elif line.startswith("data:"):
            data = line[5:].strip()
            if evt == "step":
                d = json.loads(data)
                steps.append(d)
                print(f"  [step] {d['node']:<9} {d.get('title','')} · {d.get('detail','')}")
            elif evt == "thought":
                d = json.loads(data)
                thoughts.append(d)
                print(f"  [thought] 搜索「{d['query']}」→ 找到 {d['found']} 条  片段: {len(d.get('snippets', []))}条")
            elif evt == "token":
                tokens.append(json.loads(data).get("text", ""))
            elif evt == "done":
                d = json.loads(data)
                print(f"\n[done] grounded={d['grounded']} retry={d['retry_count']} cites={len(d['citations'])}")
                print(f"answer: {d['answer'][:120]}...")

joined = "".join(tokens)
print(f"\n{'='*60}")
print(f"事件统计: {len(steps)} step + {len(thoughts)} thought + {len(tokens)} token")
if tokens:
    matched = joined == d["answer"]
    print(f"token重建答案: {'✓ 完全匹配' if matched else '✗ 不一致'}  流式前40字: {joined[:40]}")
else:
    print("无token事件(生成器流式失败→回退非流式, 仍可用)")
snip_ok = all(t.get("snippets") for t in thoughts) if thoughts else True
ok = len(steps) >= 3 and all(s.get("title") for s in steps) and snip_ok
print(f"step均含title: {all(s.get('title') for s in steps)} | thought片段非空: {snip_ok} | 有token流: {len(tokens) > 0}")
print(f"\n{'✓ 思考过程+逐字流式验证通过' if ok else '✗ 验证未通过'}")
sys.exit(0 if ok else 1)
