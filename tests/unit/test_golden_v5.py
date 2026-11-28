"""golden v5 数据完整性(P1②评测效度 + v5多轮链路) — 纯数据断言, 无外部服务, CI可跑."""

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
    assert GOLDEN_VERSION == "v5"
    assert len(GOLDEN) == 120, "v5 = v4的100问 + 20多轮问"
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
    qs = [g["q"] for g in GOLDEN]
    assert len(qs) == len(set(qs)), "存在逐字重复问题"


def test_keyword_queries_contain_ascii_tokens():
    """清单不变式: 单轮段尾部8问为关键词型(含ASCII精确token) — BM25稀疏路应发挥的查询类型."""
    tail8 = GOLDEN[92:100]
    assert len(tail8) == 8 and not any(g.get("history") for g in tail8)
    for g in tail8:
        assert re.search(r"[A-Za-z]", g["q"]), f"关键词型问句无ASCII token: {g['q']}"


def test_multi_turn_integrity():
    """v5核心: 多轮问必须有非空history(question/answer齐全), follow-up含指代, 且指代能由history消解."""
    mt = [g for g in GOLDEN if g.get("history")]
    assert len(mt) == 20, f"v5多轮问应20个, 实际{len(mt)}"
    for g in mt:
        assert isinstance(g["history"], list) and g["history"], f"history为空: {g['q']}"
        for h in g["history"]:
            assert (h.get("question") or "").strip() and (h.get("answer") or "").strip(), \
                f"history轮次缺question/answer: {g['q']}"
        assert re.search(r"它|那[个座道家]|这[个座道]|该|此", g["q"]), \
            f"follow-up无指代词(不是多轮模式): {g['q']}"
        assert not (g["q"] in UNANSWERABLE)


def test_multi_turn_followups_not_duplicates_of_single_turn():
    """follow-up不得与单轮问逐字重复(去重测试只查全清单, 这里保证多轮独立有效性)."""
    mt_qs = {g["q"] for g in GOLDEN if g.get("history")}
    st_qs = {g["q"] for g in GOLDEN if not g.get("history")}
    assert not (mt_qs & st_qs)


def test_unanswerable_structured_and_disjoint():
    golden_qs = {g["q"] for g in GOLDEN}
    assert not (set(UNANSWERABLE) & golden_qs), "不可答问题不得与GOLDEN重复"
    assert all(q.strip() for q in UNANSWERABLE)
