# src/prompts: 提示词模块 - 每个提示词独立文件，按功能分类

# CRAG提示词
from src.prompts.crag import (
    GRADER_PROMPT,
    GRADER_BATCH_PROMPT,
    GENERATOR_PROMPT,
    VERIFIER_PROMPT,
    REWRITE_PROMPT,
)

# Agent提示词
from src.prompts.agent import (
    TOOL_AGENT_PROMPT,
    PLAN_PROMPT,
    AGENT_COMPLEX_JUDGE_PROMPT,
    CHITCHAT_PROMPT,
    OPEN_ANSWER_FALLBACK_PROMPT,
    FACT_EXTRACT_PROMPT,
    ABSTRACT_PROMPT,
)

# 评测judge提示词(独立评审, 与被评系统异模型)
from src.prompts.eval_judge import JUDGE_PROMPT

__all__ = [
    # CRAG
    "GRADER_PROMPT",
    "GRADER_BATCH_PROMPT",
    "GENERATOR_PROMPT",
    "VERIFIER_PROMPT",
    "REWRITE_PROMPT",
    # Agent
    "TOOL_AGENT_PROMPT",
    "PLAN_PROMPT",
    "AGENT_COMPLEX_JUDGE_PROMPT",
    "CHITCHAT_PROMPT",
    "OPEN_ANSWER_FALLBACK_PROMPT",
    "FACT_EXTRACT_PROMPT",
    "ABSTRACT_PROMPT",
    # 评测judge
    "JUDGE_PROMPT",
]
