# 架构优化⑤: 双模型集成reranker单测 — 排序权=排序模型, 拒答门控=refuser分数覆写
# 关键语义: 两模型排序可能不同, 分数必须按chunk_id映射(位置zip会错配)
import pytest

import src.retrieval.reranker as RR
from src.core.config import get_settings
from src.storage.protocol import RetrievedDocument


def _doc(cid, content):
    return RetrievedDocument(chunk_id=cid, document_id=f"d_{cid}", version_id="v", parent_id=None,
                             content=content, locator={}, score=0.5, retrieval_source="hybrid_rrf")


class FakeResp:
    def __init__(self, results):
        self._results = results

    def raise_for_status(self):
        pass

    def json(self):
        return {"results": self._results}


@pytest.mark.asyncio
async def test_ensemble_overrides_scores_by_chunk_id(monkeypatch):
    """Qwen排序(A>B>C) + bge重打分(C=0.9,A=0.05,B=0.01) → 顺序保留A,B,C, 分数=refuser分布."""
    s = get_settings()
    monkeypatch.setattr(s, "rerank_model", "Qwen/Qwen3-Reranker-4B")
    monkeypatch.setattr(s, "rerank_ensemble_refuser", True)
    monkeypatch.setattr(s, "rerank_refuser_model", "BAAI/bge-reranker-v2-m3")
    monkeypatch.setattr(s, "rerank_selfheal_min", 0.30)

    docs = [_doc("A", "文档甲"), _doc("B", "文档乙"), _doc("C", "文档丙")]
    calls: list[str] = []

    def fake_post(model):
        calls.append(model)
        if model == "Qwen/Qwen3-Reranker-4B":  # 排序: A>B>C
            return FakeResp([{"index": 0, "relevance_score": 0.99},
                             {"index": 1, "relevance_score": 0.98},
                             {"index": 2, "relevance_score": 0.97}])
        # bge重打分: C高分(边界问半匹配场景), A/B近零(负样本签名)
        return FakeResp([{"index": 2, "relevance_score": 0.90},
                         {"index": 0, "relevance_score": 0.05},
                         {"index": 1, "relevance_score": 0.01}])

    class FakeClient:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            return fake_post(json["model"])

    monkeypatch.setattr(RR.httpx, "AsyncClient", FakeClient)
    out = await RR.rerank("测试问题", docs, top_n=3)
    assert calls == ["Qwen/Qwen3-Reranker-4B", "BAAI/bge-reranker-v2-m3"], "先排序后refuser"
    assert [d.chunk_id for d in out] == ["A", "B", "C"], "顺序必须保留排序模型的结果"
    assert [d.score for d in out] == [0.05, 0.01, 0.90], "分数必须按chunk_id覆写为refuser分布"
    assert all(d.retrieval_source == "rerank_ensemble" for d in out)


@pytest.mark.asyncio
async def test_ensemble_disabled_single_call(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "rerank_ensemble_refuser", False)
    docs = [_doc("A", "文档甲")]
    calls: list[str] = []

    def fake_post(model):
        calls.append(model)
        return FakeResp([{"index": 0, "relevance_score": 0.99}])

    class FakeClient:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            return fake_post(json["model"])

    monkeypatch.setattr(RR.httpx, "AsyncClient", FakeClient)
    out = await RR.rerank("测试问题", docs, top_n=1)
    assert calls == [s.rerank_model], "开关关闭只调排序模型一次"
    assert out[0].retrieval_source == "rerank"


@pytest.mark.asyncio
async def test_ensemble_refuser_failure_degrades_gracefully(monkeypatch):
    """refuser调用失败 → 保留排序模型分数(降级为单模型), 不阻塞主流程."""
    s = get_settings()
    monkeypatch.setattr(s, "rerank_model", "Qwen/Qwen3-Reranker-4B")
    monkeypatch.setattr(s, "rerank_ensemble_refuser", True)
    monkeypatch.setattr(s, "rerank_refuser_model", "BAAI/bge-reranker-v2-m3")
    monkeypatch.setattr(s, "rerank_selfheal_min", 0.30)

    docs = [_doc("A", "文档甲"), _doc("B", "文档乙")]

    def fake_post(model):
        if model == "BAAI/bge-reranker-v2-m3":
            raise RuntimeError("refuser端点超时")
        return FakeResp([{"index": 0, "relevance_score": 0.99},
                         {"index": 1, "relevance_score": 0.40}])

    class FakeClient:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None):
            return fake_post(json["model"])

    monkeypatch.setattr(RR.httpx, "AsyncClient", FakeClient)
    out = await RR.rerank("测试问题", docs, top_n=2)
    assert [d.chunk_id for d in out] == ["A", "B"]
    assert out[0].score == 0.99 and out[0].retrieval_source == "rerank", \
        "refuser失败必须保留排序模型分数与来源标记"
