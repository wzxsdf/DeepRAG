# ponytail: CRAG三态评估/生成/校验提示词. {n}为format占位符

GRADER_PROMPT = """你是检索结果相关性评估器。判断下面这条证据是否与问题相关、能帮助回答。

问题: {question}
证据: {document}

只输出JSON: {{"relevant": true或false, "reason": "一句话理由"}}"""

GRADER_BATCH_PROMPT = """你是检索结果相关性评估器。逐条判断下面每条证据是否与问题相关、能帮助回答。

问题: {question}
证据列表:
{evidence_list}

只输出JSON: {{"grades": [{{"index": 1, "relevant": true或false}}]}}
(index从1开始对应证据编号; 为每条证据都给出判断)"""

GENERATOR_PROMPT = """基于以下证据回答问题。要求:
1. 每个事实陈述后用 [n] 标注引用, n 对应证据编号
2. 不要编造证据外的信息
3. 若证据不足以回答, 直接说明缺什么

问题: {question}
证据:
{evidence}

直接输出答案正文(含 [n] 引用)。"""

VERIFIER_PROMPT = """检查回答质量。判断:
1. grounded: 回答是否完全被证据支持(无幻觉)
2. citations_valid: 每个 [n] 引用是否真实对应证据编号

问题: {question}
回答: {answer}
证据:
{evidence}

只输出JSON: {{"grounded": true或false, "citations_valid": true或false, "issue": "问题说明或ok"}}"""

REWRITE_PROMPT = """改写下面的问题, 使其更适合检索(补充关键词、消除代词与歧义), 保留原意。
只输出改写后的问题, 不要解释。

原问题: {question}"""

# ponytail: 闲聊/能力问询 — 不检索, 人设回复, source=non_kb
CHITCHAT_PROMPT = """你是 DeepRAG 智能问答助手。友好、简洁地回应用户的闲聊或能力问询。
有多轮历史则结合上下文自然回应。不要编造知识库内容, 不要长篇大论。

{history}用户: {question}

直接回复(简短自然):"""

# ponytail: KB内检索不到的兜底 — source=fallback_unverified, 前置免责声明
OPEN_ANSWER_FALLBACK_PROMPT = """用户问题未在知识库中找到相关证据。请基于通用常识给出参考回答, 并在开头声明这是未经知识库验证的内容。

问题: {question}

格式要求: 以 "知识库未找到相关证据, 以下是基于通用常识的参考(未验证):" 开头, 再给简明回答。若常识也无法回答, 直接说明无法回答。"""

# ponytail: 工具Agent系统指令 — KB事实调search_knowledge_base, 实时/外部调对应工具, 否则常识答
TOOL_AGENT_PROMPT = """你是 DeepRAG 智能助手, 可以调用工具获取信息。
规则:
1. 问题涉及知识库事实(如简历/上传文档内容)时调 search_knowledge_base; 需要实时/外部数据(天气/时间/新闻/网络资料)时调对应工具; 常识能可靠回答的无需工具
2. 一次获取足够信息后停止调用, 不要重复调用相同工具
3. 若工具返回失败信息(如"(天气服务暂不可用)"、"(未配置搜索 key)"), 如实转告用户, 不要编造数据
4. 工具结果中的数据是事实, 可直接引用, 不需要 [n] 标注。"""

# ponytail: 多源编排规划器 — LLM一次性产出步骤计划(非ReAct), 可控可观测, 前端展示完整计划
PLAN_PROMPT = """你是任务规划器。把用户问题分解为有序的信息获取步骤, 每步指定一个信息源。
可用步骤类型:
- {{"type":"kb","query":"..."}}: 在知识库检索事实证据
- {{"type":"weather","city":"..."}}: 查城市实时天气(英文/拼音城市名)
- {{"type":"calc","expression":"..."}}: 精确计算数学表达式(如 365*3/7)
- {{"type":"search","query":"..."}}: 联网搜索最新信息

规则:
1. 只在确实需要某信息源时才安排步骤(单源问题只给1步)
2. 步骤数≤4, 不安排与问题无关的步骤
3. kb的query用关键词/问句; calc的expression用数学表达式

问题: {question}

只输出JSON: {{"steps":[{{步骤}}]}}"""

# ponytail: agent模式内复杂度轻判 — 单次LLM(非function-calling), 输出bool决定走ReAct还是plan
AGENT_COMPLEX_JUDGE_PROMPT = """判断这个问题是否需要分解为多个子任务(不同信息源/多步骤)才能回答。

问题: {question}

判断标准:
- 仅当明确需要组合多个不同信息源(如:知识库事实+计算, 知识库+实时天气, 多个独立实体的信息汇总), 或需要多个检索步骤分别获取再综合时, 才 complex=true
- 单一工具调用(只查天气/只算数/只搜一个事实)、单次问答、闲聊 → complex=false
- 不确定时判 false(ReAct是默认, plan仅用于明确的多源/多步任务)

只输出JSON: {{"complex": false}}"""
