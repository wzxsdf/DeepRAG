# CRAG提示词 - 检索增强生成的核心提示词

# ponytail: {n}为format占位符

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


__all__ = [
    "GRADER_PROMPT",
    "GRADER_BATCH_PROMPT",
    "GENERATOR_PROMPT",
    "VERIFIER_PROMPT",
    "REWRITE_PROMPT",
]
