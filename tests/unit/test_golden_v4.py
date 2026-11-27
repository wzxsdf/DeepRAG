"""golden v4 数据完整性(P1②评测效度) — 纯数据断言, 无外部服务, CI可跑."""

import re

from src.evaluation.golden import (
    DISTRACTOR_IDS,
    DOCUMENTS,
    GOLDEN,
    GOLDEN_VERSION,
    TOPIC_IDS,
    UNANSWERABLE,
)

DOC_IDS = {doc_id for doc_id, _ in DOCUMENTS}


def test_version_and_scale():
    assert GOLDEN_VERSION == "v4"
    assert len(GOLDEN) == 100, "v4 = v3的62问 + 38新问(20口语+8跨簇+10关键词)"
    assert len(UNANSWERABLE) >= 18


def test_all_questions_have_reference_answer():
    """P1②核心: 每问必须带非空参考答案(独立judge的评分基准)."""
    missing = [g["q"] for g in GOLDEN if not (g.get("answer") or "").strip()]
    assert not missing, f"缺参考答案: {missing}"


def test_relevant_only_topic_docs_and_exist():
    """relevant只允许主题文档(干扰项永不相关)且必须真实存在."""
    for g in GOLDEN:
        assert g["relevant"], g["q"]
        for rid in g["relevant"]:
            assert rid in DOC_IDS, f"{g['q']} 引用不存在的文档 {rid}"
            assert rid in TOPIC_IDS, f"{g['q']} 引用了干扰项 {rid}(违反设计规则)"


def test_all_topics_covered():
    covered = {rid for g in GOLDEN for rid in g["relevant"]}
    assert TOPIC_IDS <= covered, f"未覆盖主题: {TOPIC_IDS - covered}"


def test_no_duplicate_questions():
    """v4新增: 逐字重复问会虚增样本量/污染逐问配对对照."""
    qs = [g["q"] for g in GOLDEN]
    assert len(qs) == len(set(qs)), "存在逐字重复问题"


def test_keyword_queries_contain_ascii_tokens():
    """清单不变式: 尾部8问为关键词型(含ASCII精确token) — BM25稀疏路应发挥的查询类型."""
    tail8 = GOLDEN[-8:]
    for g in tail8:
        assert re.search(r"[A-Za-z]", g["q"]), f"关键词型问句无ASCII token: {g['q']}"


def test_unanswerable_structured_and_disjoint():
    golden_qs = {g["q"] for g in GOLDEN}
    assert not (set(UNANSWERABLE) & golden_qs), "不可答问题不得与GOLDEN重复"
    assert all(q.strip() for q in UNANSWERABLE)
