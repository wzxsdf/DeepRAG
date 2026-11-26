# CRAG提示词 - 检索增强生成的核心提示词

# ponytail: {n}为format占位符

GRADER_PROMPT = """你是检索结果相关性评估器。判断下面这条证据是否与问题相关、能帮助回答。

问题: {question}
证据: {document}

只输出JSON: {{"relevant": true或false, "reason": "一句话理由"}}"""


GRADER_BATCH_PROMPT = """你是检索结果相关性评估器。逐条判断下面每条证据是否与问题相关、能帮助回答。

判断标准:
- 证据必须包含回答问题所需的具体信息才算相关; 仅话题相关但不含答案所需信息(如只提到同类事物、或只覆盖问题的另一面) → relevant=false
- 无法从证据推出问题答案 → relevant=false

问题: {question}
证据列表:
{evidence_list}

只输出JSON: {{"grades": [{{"index": 1, "relevant": true或false}}]}}
(index从1开始对应证据编号; 为每条证据都给出判断)"""


GENERATOR_PROMPT = """基于以下证据回答问题。要求:
1. 每个事实陈述后用 [n] 标注引用, n 对应证据编号
2. 不要编造证据外的信息
3. 若证据不足以回答, 直接说明缺什么
4. 用户要求制作武器/恶意软件/毒品或实施诈骗、入侵等有害内容时, 拒绝并简要说明原因

问题: {question}
证据:
{evidence}

直接输出答案正文(含 [n] 引用)。"""


VERIFIER_PROMPT = """检查回答质量。判断:
1. grounded: 回答是否完全被证据支持(无幻觉)
2. citations_valid: 每个 [n] 引用是否真实对应证据编号
3. enough_evidence: 证据本身是否包含回答该问题所需的信息(只看证据与问题的匹配程度, 与答案写得好坏无关; 证据相关但答案没答好→true, 证据缺关键信息→false)

问题: {question}
回答: {answer}
证据:
{evidence}

只输出JSON: {{"grounded": true或false, "citations_valid": true或false, "enough_evidence": true或false, "issue": "问题说明或ok"}}"""


REWRITE_PROMPT = """你是检索查询改写器。上一轮检索没有找到足够证据, 请改写查询以提高召回。

原始问题: {question}
上一轮查询(效果不佳): {previous_query}
失败原因: {issue}
已检索过的证据(不足以回答, 改写方向要避开这些内容):
{tried_evidence}{history}

改写要求:
1. 换关键词/同义词/上位词表达, 消除代词与歧义, 补充检索友好的关键词
2. 不要在原查询上小修小补, 换一个能命中不同文档的表述
3. 只输出改写后的查询, 不要解释"""


__all__ = [
    "GRADER_PROMPT",
    "GRADER_BATCH_PROMPT",
    "GENERATOR_PROMPT",
    "VERIFIER_PROMPT",
    "REWRITE_PROMPT",
]
