# ponytail: 独立judge评审提示词(评测效度补齐, 20261126) — 与被评系统异模型异构, 替代verifier自评
# 使用方式: ablation经 chat_json(..., purpose="judge") 走 CHAT_JUDGE_MODEL 配置的模型;
# CHAT_JUDGE_MODEL 为空则 judge 关闭(回退 verifier 自评口径, 报告标注 ★自评)

JUDGE_PROMPT = """你是独立的答案质量评审, 与被评的问答系统无关。对照参考答案与证据, 对系统回答打分。

问题: {question}
参考答案: {reference}
系统回答: {answer}
证据:
{evidence}

评分规则(每项true/false):
- answer_correct: 系统回答与参考答案语义一致(允许表述不同, 关键事实必须一致; 遗漏次要细节不影响)
- grounded: 系统回答的每个事实陈述都被证据支持, 无证据外的编造
- citation_correct: 回答中的[n]引用指向的证据确实支持对应陈述; 若回答无事实错误且证据充分支持, 视为true

只输出JSON: {{"answer_correct": true或false, "grounded": true或false, "citation_correct": true或false, "reason": "一句话"}}"""


__all__ = ["JUDGE_PROMPT"]
