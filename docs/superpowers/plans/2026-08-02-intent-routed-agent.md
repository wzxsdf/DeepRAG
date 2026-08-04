# 意图分层路由 实现计划

> **For agentic workers:** 用 superpowers:executing-plans 逐任务实现。步骤用 `- [ ]` 跟踪。

**Goal:** 把纯 CRAG 检索系统升级为意图分层的问答智能体——闲聊/能力/常识能答且来源透明标注，KB 事实仍严格 grounded。

**Architecture:** route 节点从"判复杂度"扩为"判 6 类意图"（规则快通道 + LLM 兜底），新增两条非检索路径 `reply_direct`/`open_answer`，grade 无证据时改走 open_answer 软拒兜底。新增 `source` 字段端到端透传到前端 4 色徽章。

**Tech Stack:** LangGraph 1.2.10（Command 路由）、juaiapi（chat/chat_json/chat_stream）、FastAPI SSE、React + Framer Motion。

## Global Constraints

- 注释中文，与现有代码一致；刻意简化标 `ponytail:`。
- **不主动 git commit/branch**（项目非 git 仓库 + 用户约束）。
- 测试遵循现有 `scripts/test_*.py` 端到端风格（httpx + assert，连真实后端），不引入 pytest 框架。
- 不破坏现有 simple/complex/grade/verify/generate/rewrite 可信硬核。
- LLM 调用走 `src/agents/llm.py`（chat/chat_json/chat_stream/chat_message），不直连。

## File Structure

| 文件 | 职责 | 改动 |
|---|---|---|
| `src/agents/prompts.py` | 所有提示词 | 改 ROUTER 为 6 类；新增 CHITCHAT/OPEN_ANSWER/OPEN_ANSWER_FALLBACK |
| `src/agents/graph.py` | CRAG 状态机 | state 加 intent/source；抽 `_stream_answer` helper；规则快通道 `_classify_by_rule`；route 改 Command 分流；新增 reply_direct/open_answer；grade 改软拒跳转；verify 降级 |
| `apps/api/query.py` | 查询接口 | `_step_detail` 加新节点；done/QueryResponse 加 source；initial state 补字段 |
| `web/src/api/chat.ts` | 前端类型 | `StreamDone` 加 source |
| `web/src/pages/Chat.tsx` | 对话页 | Msg 加 source；4 色来源徽章（替代 grounded 徽章） |
| `scripts/test_intent_routing.py` | 端到端测试 | 新增，验证 6 类分流 + source + 不白检索 |

## 接口契约（跨任务依赖）

- `_classify_by_rule(question: str) -> str | None`：返回 `"chitchat"`/`"capability"`/`None`（高 precision，漏则走 LLM）
- `_stream_answer(messages: list[dict]) -> str`：流式生成 + writer 推 token + 兜底非流式；generate/reply_direct/open_answer 共用
- `AgentState` 新增：`intent: str`、`source: str`
- `source` 取值：`kb_grounded` | `non_kb` | `open_qa` | `fallback_unverified` | `refused`
- route → `Command(update={intent, complexity, sub_queries}, goto=...)`，goto ∈ {`reply_direct`, `open_answer`, `retrieve`, `END`}
- grade 无相关 → `Command(goto="open_answer")`（不再 END）
- open_answer：`source = "open_qa" if intent=="open_qa" else "fallback_unverified"`

---

### Task 1: prompts.py — 意图分类 + 非检索路径提示词

**Files:** Modify `src/agents/prompts.py`

**Produces:** `ROUTER_PROMPT`（6 类）、`CHITCHAT_PROMPT`、`OPEN_ANSWER_PROMPT`、`OPEN_ANSWER_FALLBACK_PROMPT`

- [ ] 把 `ROUTER_PROMPT` 改为判 6 类意图（chitchat/capability/kb_simple/kb_complex/open_qa/out_of_scope），仍输出 complexity + sub_queries（complex 供 retrieve 复用）
- [ ] 新增 `CHITCHAT_PROMPT`（人设：DeepRAG 智能问答助手，友好简短，带历史多轮）
- [ ] 新增 `OPEN_ANSWER_PROMPT`（常识答 + 安全护栏：拒实时/医疗法律/隐私/违法，检测到越界回 "REFUSED"）
- [ ] 新增 `OPEN_ANSWER_FALLBACK_PROMPT`（KB 未找到证据的兜底，前置声明"知识库未找到，以下未验证参考"）

**关键 ROUTER 输出格式：**
```json
{"intent": "chitchat|capability|kb_simple|kb_complex|open_qa|out_of_scope",
 "complexity": "simple|complex", "sub_queries": ["..."]}
```

---

### Task 2: graph.py — state 字段 + helper + 规则快通道

**Files:** Modify `src/agents/graph.py`
**Consumes:** Task 1 的 prompts
**Produces:** `AgentState.intent/source`、`_stream_answer`、`_classify_by_rule`（带 `__main__` assert 自检）

- [ ] `AgentState` 加 `intent: str`、`source: str`
- [ ] 抽 `_stream_answer(messages)` helper（从 `_generate_node` 提炼流式逻辑，DRY）；`_generate_node` 改用它
- [ ] 加 `_classify_by_rule(question)` + 两条正则（CHITCHAT_RE/CAPABILITY_RE）
- [ ] 加 `if __name__ == "__main__"` assert 自检（`你好`→chitchat，`你能做什么`→capability，`什么是RAG`→None）
- [ ] 运行 `python -m src.agents.graph`，预期打印 `rule classify ok`

---

### Task 3: graph.py — route 改 Command 分流

**Files:** Modify `src/agents/graph.py:_route_node`
**Consumes:** Task 2 的 `_classify_by_rule`、Task 1 的 ROUTER_PROMPT
**Produces:** route 返回 `Command`，按 intent 分流到 reply_direct/open_answer/retrieve/END

- [ ] `_route_node` 改为 `async def -> Command`：先 `_classify_by_rule`，命中→直接 Command(goto=reply_direct)；未命中→chat_json(ROUTER_PROMPT) 得 intent，按 intent Command 分流；out_of_scope→Command(update={generation:拒答文案, source:refused}, goto=END)
- [ ] chat_json 异常 fallback：intent=kb_simple, goto=retrieve（保守走检索，不阻塞）
- [ ] 移除 `wf.add_edge("route", "retrieve")`（改由 route 内 Command 决定）

---

### Task 4: graph.py — 新节点 + grade/verify 改造 + 接线

**Files:** Modify `src/agents/graph.py`
**Consumes:** Task 1 的 CHITCHAT/OPEN_ANSWER prompts、Task 2 的 `_stream_answer`
**Produces:** `reply_direct`、`open_answer` 节点；grade 软拒跳转；verify 降级；完整状态机接线

- [ ] `_reply_direct_node`：source=non_kb，`_stream_answer(CHITCHAT_PROMPT)`，goto END
- [ ] `_open_answer_node`：source 按 intent 判（open_qa/fallback_unverified），选对应 prompt，`_stream_answer`，goto END
- [ ] `_grade_node`：docs 空 / 无相关 → `Command(goto="open_answer")`（不再写死 END 拒绝文案）
- [ ] `_verify_node`：grounded 通过 → update source=kb_grounded；达上限 → source=fallback_unverified
- [ ] `build_graph`：add_node reply_direct/open_answer；add_edge(reply_direct, END)、(open_answer, END)、(generate, verify) 不变
- [ ] 启动后端，跑现有 `scripts/test_thinking_stream.py` 回归（complex 路径不破）

---

### Task 5: query.py — source 透传 + step 详情

**Files:** Modify `apps/api/query.py`
**Consumes:** Task 4 的 source 字段
**Produces:** done 事件 / QueryResponse 带 source；新节点 step 详情；initial state 补字段

- [ ] `QueryResponse` 加 `source: str`
- [ ] `/query` 返回 `source=values.get("source","")`
- [ ] `/query/stream` done 事件加 `"source": values.get("source","")`
- [ ] 两个 initial state dict 加 `"intent": ""`, `"source": ""`
- [ ] `_step_detail` 加 `reply_direct`（"直接回复"）/`open_answer`（"常识回答"/"兜底回答"）分支
- [ ] 启动后端，curl 闲聊问题，done 事件含 `source:"non_kb"`

---

### Task 6: 前端 — source 类型 + 4 色徽章

**Files:** Modify `web/src/api/chat.ts`、`web/src/pages/Chat.tsx`
**Consumes:** Task 5 的 source

- [ ] `chat.ts`：`StreamDone` 加 `source: string`
- [ ] `Chat.tsx`：`Msg` 加 `source?`；send 的 done 回调写入 `source`
- [ ] 加 `SOURCE_META` 映射（source→{label,color,icon}）：kb_grounded 绿/ non_kb 白/ open_qa+fallback_unverified 黄/ refused 红
- [ ] 渲染：用 source 徽章替代现有 grounded 徽章；无 source 的历史消息 fallback 到 grounded 推断
- [ ] `npm run dev`，问"你好"看到⚪非知识库徽章；问 KB 问题看到🟢已引用

---

### Task 7: 端到端测试 test_intent_routing.py

**Files:** Create `scripts/test_intent_routing.py`
**Consumes:** 全部前序任务

- [ ] 6 类各 1 用例：`你好`(chitchat/non_kb)、`你能做什么`(capability/non_kb)、`什么是RAG`(open_qa)、KB 简单事实(kb_grounded)、KB 多跳(kb_grounded)、越界(refused)
- [ ] 断言：①最终 source 正确 ②闲聊/open_qa 的 SSE step 流里**无 retrieve 节点**（不白检索）③KB 问题 done 含 citations
- [ ] 运行 `python scripts/test_intent_routing.py`，全部 assert 通过

---

## Self-Review

- **Spec coverage：** spec 第 5 节状态机 → Task 3+4；第 6 节组件清单 → Task 2-6 逐项；第 7 节护栏 → Task 1 OPEN_ANSWER_PROMPT；第 8 节 source → Task 2/4/5/6；第 10 节测试 → Task 7 + 各任务回归。✓ 无遗漏。
- **Placeholder：** 各任务均有具体代码契约/验证命令，无 TBD。✓
- **类型一致：** `source` 5 取值在 graph/query/chat.ts/Chat.tsx 全程一致；`intent` 6 类在 ROUTER/route/_classify_by_rule 一致；`_stream_answer` 三处调用签名一致。✓
