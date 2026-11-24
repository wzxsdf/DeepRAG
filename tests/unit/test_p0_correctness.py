"""P0 correctness regressions without external services."""

import pytest

from src.cache.query_cache import QueryCache
from src.retrieval.reranker import deduplicate_documents, rerank
from src.storage.protocol import RetrievedDocument
from src.storage.qdrant_store import QdrantWriter


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.ttls = {}

    async def get(self, key):
        return self.values.get(key)

    async def setex(self, key, ttl, value):
        self.values[key] = value
        self.ttls[key] = ttl

    async def incr(self, key):
        value = int(self.values.get(key, 0)) + 1
        self.values[key] = str(value)
        return value

    async def delete(self, key):
        self.values.pop(key, None)
        self.ttls.pop(key, None)

    async def ttl(self, key):
        return self.ttls.get(key, -2)

    async def scan_iter(self, match=None, count=100):
        prefix = match[:-1] if match and match.endswith("*") else match
        for key in list(self.values):
            if prefix is None or key.startswith(prefix):
                yield key


@pytest.fixture
def cache():
    instance = QueryCache(enable_semantic=True)
    instance._redis_client = FakeRedis()
    return instance


@pytest.mark.asyncio
async def test_cache_generation_invalidates_exact_and_semantic(cache):
    filters = {"tenant_id": "tenant-a", "kb_ids": ["kb-1"]}
    await cache.set("项目检索技术", {"answer": "old"}, filters, "kb")
    assert (await cache.get("项目检索技术", filters, "kb"))["answer"] == "old"

    await cache.bump_generation("tenant-a")
    assert await cache.get("项目检索技术", filters, "kb") is None
    assert await cache.get("项目检索技术的检索技术", filters, "kb") is None


@pytest.mark.asyncio
async def test_semantic_cache_does_not_cross_scope(cache):
    await cache.set(
        "DeepRAG 检索技术实现",
        {"answer": "tenant-a"},
        {"tenant_id": "tenant-a", "kb_ids": ["kb-1"]},
        "kb",
    )
    assert await cache.get(
        "DeepRAG 检索技术实现细节",
        {"tenant_id": "tenant-b", "kb_ids": ["kb-1"]},
        "kb",
    ) is None
    assert await cache.get(
        "DeepRAG 检索技术实现细节",
        {"tenant_id": "tenant-a", "kb_ids": ["kb-2"]},
        "kb",
    ) is None


def test_qdrant_filter_contains_acl_and_versions():
    query_filter = QdrantWriter._build_filter(
        "tenant-a",
        kb_ids=["kb-1"],
        acl_tags=["engineering", "internal"],
        active_version_ids=["v-1", "v-2"],
    )
    conditions = query_filter.must
    assert len(conditions) == 5
    assert conditions[0].key == "tenant_id"
    assert conditions[1].key == "kb_id"
    assert [condition.key for condition in conditions[2:4]] == ["acl_tags", "acl_tags"]
    assert conditions[4].key == "version_id"
    assert conditions[4].match.any == ["v-1", "v-2"]


def make_doc(chunk_id: str, parent_id: str | None, score: float = 0.0):
    return RetrievedDocument(
        chunk_id=chunk_id,
        document_id="doc-1",
        version_id="v-1",
        parent_id=parent_id,
        content=chunk_id,
        locator={},
        score=score,
        retrieval_source="hybrid_rrf",
    )


def test_parent_dedup_keeps_first_ranked_child():
    docs = [make_doc("c1", "p1"), make_doc("c2", "p1"), make_doc("c3", None)]
    result = deduplicate_documents(docs)
    assert [doc.chunk_id for doc in result] == ["c1", "c3"]


@pytest.mark.asyncio
async def test_rerank_fallback_deduplicates(monkeypatch):
    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            raise RuntimeError("rerank unavailable")

    monkeypatch.setattr("src.retrieval.reranker.httpx.AsyncClient", FakeAsyncClient)
    docs = [make_doc("c1", "p1"), make_doc("c2", "p1"), make_doc("c3", "p2")]
    result = await rerank("问题", docs, top_n=5)
    assert [doc.chunk_id for doc in result] == ["c1", "c3"]
