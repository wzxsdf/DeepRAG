# Agent提示词 - Agent系统专用提示词


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


# ponytail: P2 记忆写入 — 一次LLM同时完成: 抽取结构化事实 + 判断价值 + 摘要(三合一, 省2次调用)
# C1: facts 用受控标准key(防碎片化); G1: 禁抽敏感信息
FACT_EXTRACT_PROMPT = """从以下对话中抽取值得长期记住的信息(供未来对话参考)。

对话:
用户: {question}
助手: {answer}

判断与抽取规则:
1. facts: 从对话中提炼的用户结构化事实(偏好/身份/技术栈/项目状态)。
   - key 必须用以下标准词之一(无合适的则不抽, 不要自造key):
     preferred_language(编程语言) / framework(框架) / role(职业角色) / timezone(时区)
     / current_project(当前项目) / domain(领域) / experience_level(水平) / other(其他)
   - value 简短(一个词或短语)。例: {{"preferred_language":"Python","framework":"FastAPI","role":"后端开发"}}
   - 仅抽用户明确表达或可确定的事实, 不臆测; 无则空对象 {{}}
2. 敏感信息禁抽(G1): 不得把密码/密钥/token/身份证号/银行卡号/手机号等写入 facts。
3. memorable: 这轮对话是否产生了值得跨会话记住的结论或事件(关键决策/重要事实/有价值的任务结果)。
   - 纯闲聊/简单问答/无信息量 → false; 包含用户偏好/项目背景/重要结论 → true
4. summary: 仅当 memorable=true 时, 用一句话概括这轮对话的要点(供未来语义检索); 否则空串。

只输出JSON: {{"facts":{{}},"memorable":false,"summary":""}}"""


# ponytail: P3 记忆整合 — 情节记忆→语义记忆的抽象提炼(信息密度提升)
ABSTRACT_PROMPT = """以下是一个用户的多条历史交互记录(情节记忆)。请从中提炼出通用的规律、偏好或知识(语义记忆)。

情节记忆:
{episodes}

提炼规则:
1. 只提炼能跨场景复用的通用结论(如"用户偏好简洁代码""涉及X时倾向用Y方案")
2. 不要罗列具体事件, 要抽象成规律性认知
3. 若情节之间有时间上的矛盾(如早期偏好A后来改B), 以时间更新的为准(冲突消解)
4. 提炼出的每条语义记忆应独立完整, 一行一条
5. 若无法提炼出有价值的规律, 直接回复: NONE

直接输出提炼结果(每行一条), 或 NONE:"""


__all__ = [
    "CHITCHAT_PROMPT",
    "OPEN_ANSWER_FALLBACK_PROMPT",
    "TOOL_AGENT_PROMPT",
    "PLAN_PROMPT",
    "AGENT_COMPLEX_JUDGE_PROMPT",
    "FACT_EXTRACT_PROMPT",
    "ABSTRACT_PROMPT",
]
