# 意图分层路由：从 CRAG 检索系统升级为基于 RAG 的问答智能体

- **日期**：2026-08-02
- **状态**：已设计，待实现
- **定位决策**：B 混合开放（KB 内严格 grounded + 闲聊/常识开放对话，来源透明标注）
- **判定决策**：B 规则快通道 + LLM 兜底

---

## 1. 背景与问题

当前系统是纯 RAG 检索生成系统。当用户问闲聊、问候或 KB 外常识（"你好""什么是 RAG"）时，系统回复：

> 抱歉，未在知识库中找到与该问题相关的证据，无法回答。

**根因（基于 `src/agents/graph.py` 代码事实）**：

1. `_route_node`（`:47-55`）只判 `simple/complex` 两个复杂度档，**二者都要检索**，没有"这问题需不需要检索"的意图层。闲聊/问候/常识被无条件推进 retrieve。
2. `_grade_node`（`:80-82, 95-98`）对"无相关证据"是**硬拒 `Command(goto=END)`**，文案写死。这是拒绝文案的源头。
3. 全流程**没有任何"开放对话/常识兜底"出口**：要么 grounded 生成，要么 refuse，二选一。

一句话：当前是只有"答知识库"和"拒绝"两种状态的状态机，缺少"对话"状态。

## 2. 目标与非目标

**目标**：
- 闲聊、问候、能力问询、KB 外常识都能合理回复，不再一律拒答。
- 区分意图，决定是否检索、检索多少、答什么、标什么来源。
- 不破坏现有 KB 事实问答的可信度（grounded + 引用）。
- 闲聊类响应秒级（不退化已优化的延迟）。

**非目标（YAGNI，本期不做）**：
- 独立意图分类器服务 / 标注数据集（用规则 + LLM 够）。
- 会话记忆增强、主动澄清反问、用户画像（演进项，架构不为其设限但本期不建）。
- 全开放 tool-calling agent（方向 C，留作 B 之后的演进）。

## 3. 系统定位与判定方式

### 定位：B 混合开放

| 问题类型 | 处理 | 来源标注 |
|---|---|---|
| KB 内事实 | RAG 严格 grounded + 引用 | kb_grounded |
| 闲聊 / 能力问询 | LLM 人设回复，不检索 | non_kb |
| KB 外常识 | LLM 自由答 + 安全护栏 | open_qa |
| KB 内但检索不到 | 软拒 + 常识兜底 + 免责 | fallback_unverified |
| 越界 / 有害 | 礼貌拒绝 | refused |

KB 事实严格可溯，开放对话透明标注、有免责——这是对通用 LLM 的差异化。

### 判定：B 规则快通道 + LLM 兜底

先用关键词/正则抓"100% 明显"的意图（高 precision、低 recall），命中即跳过 LLM 直进非检索路径；未命中走 LLM 6 类分类兜底。漏判无害——交给 LLM 兜底，零误伤。

## 4. 意图分类体系（6 类）

| 意图 | 例子 | 是否检索 | 处理路径 | source |
|---|---|---|---|---|
| `chitchat` 闲聊/问候/致谢 | 你好、在吗、谢谢 | ❌ | reply_direct | non_kb |
| `capability` 问能力 | 你能做什么、支持哪些格式 | ❌ | reply_direct | non_kb |
| `kb_simple` KB 内简单事实 | 简历里用了什么框架 | ✅ 单次 | 现有 simple 直通 | kb_grounded |
| `kb_complex` KB 内多跳/复杂 | 对比 A、B 项目技术栈 | ✅ 多步 | 现有 ReAct agent | kb_grounded |
| `open_qa` KB 外常识 | 什么是 RAG、Python 怎么读文件 | ❌(KB) | open_answer | open_qa |
| `out_of_scope` 越界/有害 | 写攻击脚本、无关指令 | ❌ | route 内拒答文案 | refused |

## 5. 架构（改造后状态机）

```
START → route ──(规则快通道命中: 问候/能力/致谢/越界)──┐
         │                                            │
         ├── chitchat/capability ──→ reply_direct ──→ END        [non_kb]
         ├── out_of_scope ──→ (route 内拒答文案) ──→ END          [refused]
         ├── open_qa ──→ open_answer ──→ END                     [open_qa]
         ├── kb_simple  ──┐
         └── kb_complex ──┴──→ retrieve ──→ grade
                                              │
                                   (有相关)   ├──→ generate ──→ verify ──┐
                                   (无相关)   ├──→ open_answer(兜底)──→ END [fallback_unverified]
                                                                   │
                                   verify: grounded ──→ END        [kb_grounded]
                                   verify: 不足 & retry<MAX ──→ rewrite ──→ retrieve
                                   verify: 不足 & retry 达上限 ──→ END (降级标 fallback_unverified)
```

**只新增 2 个节点**（`reply_direct`、`open_answer`），改造 2 个（`route` 扩意图、`grade` 改软拒兜底）。现有 simple/complex/grade/verify/generate/rewrite 的可信硬核完全保留。

## 6. 组件改动清单

| 组件 | 改动 | 要点 |
|---|---|---|
| `AgentState` | 加 2 字段 | `intent: str`、`source: str`（前端来源标注） |
| `_route_node` | 扩展 | 规则快通道优先（命中 chitchat/capability/out_of_scope 直接出 intent，跳过 LLM）；未命中跑 LLM 6 类分类。`complexity` 保留供 retrieve 内部分流 |
| `reply_direct`（新） | 新节点 | 处理 chitchat/capability。人设 prompt + 多轮历史，不检索，`source=non_kb` |
| `open_answer`（新） | 新节点 | 处理 open_qa **和 grade 兜底**。LLM 自由答 + 安全护栏 prompt，`source=open_qa` 或 `fallback_unverified` |
| `_grade_node` | 改跳转 | 无相关证据时 `Command(goto="open_answer")`（软拒兜底），不再硬拒 END |
| `_verify_node` | 微调 | retry 达上限仍不 grounded 时，降级标 `source=fallback_unverified`（免责） |
| 前端 `Chat.tsx` | 加来源徽章 | 按 `source` 显示 4 档徽章 |

规则快通道示例（高 precision，漏了无害——交给 LLM 兜底）：

```python
# ponytail: 只抓100%明显的意图(高precision低recall), 漏的走LLM兜底零误伤
CHITCHAT_RE = re.compile(r"你好|您好|在吗|谢谢|辛苦|嗨|hi\b|hello", re.I)
CAPABILITY_RE = re.compile(r"你(能|可以)(做|帮).*|你是谁|支持什么|怎么用")
```

## 7. 安全护栏（open_qa 边界）

`open_answer` 的 system prompt 硬约束，检测到越界则转 `out_of_scope` 文案拒绝：

- **拒答**：实时信息（股价/天气/新闻）、专业建议（医疗诊断/法律/投资决策）、个人隐私、违法违规。
- **可答**：通用常识、概念解释、编程通识、闲聊。
- **KB 内事实问题永远走 RAG grounded 路径**，不进 open_answer，保证可溯源不破坏。

## 8. 来源标注（source 字段）

5 个 source 值，4 种徽章颜色：`open_qa` 与 `fallback_unverified` 共用黄色（常识·未验证），文案区分（后者追加"知识库未找到证据"免责）。

| source | 含义 | 前端徽章 | 是否引用 |
|---|---|---|---|
| `kb_grounded` | KB 内事实，已校验 | 🟢 知识库·已引用 | ✅ |
| `non_kb` | 闲聊/能力 | ⚪ 非知识库 | ❌ |
| `open_qa` | KB 外常识 | 🟡 常识·未验证 | ❌ |
| `fallback_unverified` | KB 内检索不到的兜底 / verify 达上限降级 | 🟡 常识·未验证 | ❌ + 免责 |
| `refused` | 越界/无法回答 | 🔴 无法回答 | ❌ |

source 经流式事件透传到前端，`Chat.tsx` 按字段渲染徽章。

## 9. 数据流（各意图端到端）

| 意图 | 路径 | source | 引用 | 预期延迟 |
|---|---|---|---|---|
| chitchat（规则命中） | route→reply_direct | non_kb | 无 | ~3s |
| capability（规则命中） | route→reply_direct | non_kb | 无 | ~3s |
| open_qa | route→open_answer | open_qa | 无 | ~4s |
| kb_simple | route→retrieve→grade→generate→verify | kb_grounded | ✅ | ~13s（已优化） |
| kb_complex | route→retrieve(agent)→grade→generate→verify | kb_grounded | ✅ | ~21s（已优化） |
| KB 内检索不到 | route→retrieve→grade→open_answer(兜底) | fallback_unverified | 无+免责 | ~16s |
| out_of_scope | route→拒答文案 | refused | 无 | <1s |

## 10. 测试

- **回归不能破**：
  - `scripts/test_thinking_stream.py`（simple/complex 流式重建匹配）
  - `scripts/test_kb_flow.py`
- **新增 `scripts/test_intent_routing.py`**：6 类各 1 用例，断言：
  1. 分流到正确路径；
  2. `source` 字段正确；
  3. chitchat/open_qa 的 trace **无 retrieve 节点**（证明没白检索）；
  4. KB 内问题仍带引用。
- **护栏测试**：越界问题（"今天股价""帮我诊断病情"）走 `refused`。

## 11. 智能体演进路径（B → C）

B 是中转站。`open_answer` 节点天然可演进为**工具调用 agent**——加 `search_kb`/计算器/外部 API 等 tool，LLM 自主决定何时用，即方向 C。可叠加：会话记忆（已有 `conversation_history`）、主动澄清（模糊意图反问）、用户画像。本期不做，架构不为其设限。

## 12. 商业角度

- **差异化**：vs 通用 ChatGPT 的黑箱，"分层透明 + 来源可溯"是企业付费本质（可追责、可审计）。
- **企业版加成**：来源审计日志、按意图的 SLA（KB 事实保证引用，常识标注免责）、grounded 事实库。
- **简历叙事**：从"CRAG 检索系统"升级为"意图分层问答智能体"——多一层 Agent 路由设计 + 安全护栏 + 可信分级。

## 13. 默认决策记录（用户确认）

1. grade 检索不到 → **软拒兜底**（给常识参考 + 免责标注 `fallback_unverified`）。
2. open_qa 安全护栏拒答范围 = 实时/医疗法律/隐私/违法违规。
3. 来源徽章 = **4 档**（绿/白/黄/红）。

## 14. 实现涉及的文件（预估）

- `src/agents/graph.py` — route 扩展、新增 reply_direct/open_answer、grade 改跳转、verify 降级、AgentState 加字段
- `src/agents/prompts.py` — ROUTER_PROMPT 改 6 类、新增 CHITCHAT/OPEN_ANSWER 护栏 prompt
- `apps/api/query.py`（或流式接口处）— source 透传到 SSE 事件
- `web/src/pages/Chat.tsx`（及消息气泡组件）— 来源徽章渲染
- `scripts/test_intent_routing.py` — 新增
