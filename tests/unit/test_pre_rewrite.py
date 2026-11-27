# kb模式检索前query预改写(20261127): 集成点单测 — 改写生效/开关关闭/LLM失败兜底/门控直通
# golden v3量化缺口: 口语问(12/62)首轮召回不足被拒答阈值误降级, 预改写针对此缺口

from types import SimpleNamespace

import pytest

from src.agents.routing import needs_query_rewrite


# ---------- 0. 门控规则(纯函数): 口语命中/关键词直通/多轮强制 ----------


@pytest.mark.parametrize("q", [
    "那个输出1024维的多语言嵌入模型是什么?",
    "醋饭顶着生鱼片的那道日本料理是什么?",
    "想知道生成的答案是不是都基于检索到的原文,该看哪个指标?",
    "电车没电不去充电桩,几分钟能换电继续走的是哪家的车?",
    "存向量的系统和编码向量的模型怎么协作?",  # 无标记无ASCII → 默认改写(宁多勿漏)
])
def test_gate_rewrites_colloquial(q):
    assert needs_query_rewrite(q) is True


@pytest.mark.parametrize("q", [
    "BGE-M3输出的dense向量是多少维?",
    "Qdrant对稀疏向量做哪种统计加权?",
    "Celery的broker可以配RabbitMQ吗?",
    "DeepRAG用什么框架构建状态机?",  # 含DeepRAG精确token且无口语标记
])
def test_gate_skips_keyword_queries(q):
    assert needs_query_rewrite(q) is False


def test_gate_history_forces_rewrite():
    assert needs_query_rewrite("它输出多少维?", has_history=False) is True  # 无标记无ASCII→默认True
    assert needs_query_rewrite("BGE-M3输出多少维?", has_history=True) is True, "多轮带指代必须改写"


def test_gate_empty_question():
    assert needs_query_rewrite("  ") is False


# ---------- 1. 改写生效: retrieve收到关键词化query, 主链路不受影响 ----------


@pytest.mark.asyncio
async def test_kb_pre_rewrite_feeds_rewritten_query_to_retrieve(monkeypatch):
    import src.agents.graph as G
    import src.retrieval.reranker as RR
    from src.storage.protocol import RetrievedDocument

    captured: list[str] = []

    class FakeRetriever:
        async def retrieve(self, query, filters, top_k):
            captured.append(query)
            return [RetrievedDocument(chunk_id="c1", document_id="doc_kb", version_id="v1",
                                      parent_id=None, content="jieba基于前缀词典切分", locator={"page": 1},
                                      score=0.9, retrieval_source="rerank")]

    async def fake_rerank(query, docs, top_n=5):
        return docs

    async def fake_chat(messages):
        # 预改写调用: 返回关键词化查询
        assert "检索查询改写器" in messages[0]["content"]
        return "jieba 中文分词 使用方法\n"

    async def fake_chat_stream(messages):
        yield "用jieba[1]"

    async def fake_chat_json(messages):
        content = messages[0]["content"]
        if "检查回答质量" in content:  # verifier
            return {"grounded": True, "citations_valid": True, "enough_evidence": True, "issue": "ok"}
        return {"grades": [{"index": 1, "relevant": True}]}  # grader

    monkeypatch.setattr(RR, "rerank", fake_rerank)
    monkeypatch.setattr(G, "chat", fake_chat)
    monkeypatch.setattr(G, "chat_stream", fake_chat_stream)
    monkeypatch.setattr(G, "chat_json", fake_chat_json)

    graph = G.build_graph(FakeRetriever())
    state = {"question": "那个分词的库咋用的来着?", "query": "那个分词的库咋用的来着?",
             "tenant_id": "default", "retry_count": 0, "documents": [], "search_decision": "",
             "generation": "", "verification": None, "conversation_history": [], "kb_ids": [],
             "intent": "", "source": "", "mode": "kb", "safety": "allow",
             "user_id": "default", "memory_context": ""}
    values = await graph.ainvoke(state, config={"configurable": {"thread_id": "t-prerw-1"}})
    assert captured == ["jieba 中文分词 使用方法"], "retrieve必须收到预改写后的关键词化query"
    assert values.get("source") == "kb_grounded"
    assert values["question"] == "那个分词的库咋用的来着?", "question保持用户原话, 仅query被改写"


# ---------- 1b. 门控直通: 关键词式问题跳过改写LLM, retrieve收到原问题 ----------


@pytest.mark.asyncio
async def test_kb_pre_rewrite_gate_skips_keyword_query(monkeypatch):
    import src.agents.graph as G
    import src.retrieval.reranker as RR
    from src.storage.protocol import RetrievedDocument

    captured: list[str] = []

    class FakeRetriever:
        async def retrieve(self, query, filters, top_k):
            captured.append(query)
            return [RetrievedDocument(chunk_id="c1", document_id="doc_kb", version_id="v1",
                                      parent_id=None, content="BGE-M3输出1024维dense向量", locator={"page": 1},
                                      score=0.9, retrieval_source="rerank")]

    async def fake_rerank(query, docs, top_n=5):
        return docs

    async def must_not_chat(messages):
        raise AssertionError("关键词式问题命中门控直通, 不应调改写LLM")

    async def fake_chat_stream(messages):
        yield "1024维[1]"

    async def fake_chat_json(messages):
        content = messages[0]["content"]
        if "检查回答质量" in content:
            return {"grounded": True, "citations_valid": True, "enough_evidence": True, "issue": "ok"}
        return {"grades": [{"index": 1, "relevant": True}]}

    monkeypatch.setattr(RR, "rerank", fake_rerank)
    monkeypatch.setattr(G, "chat", must_not_chat)
    monkeypatch.setattr(G, "chat_stream", fake_chat_stream)
    monkeypatch.setattr(G, "chat_json", fake_chat_json)
    # 真实settings: gate默认开

    graph = G.build_graph(FakeRetriever())
    state = {"question": "BGE-M3输出的dense向量是多少维?", "query": "BGE-M3输出的dense向量是多少维?",
             "tenant_id": "default", "retry_count": 0, "documents": [], "search_decision": "",
             "generation": "", "verification": None, "conversation_history": [], "kb_ids": [],
             "intent": "", "source": "", "mode": "kb", "safety": "allow",
             "user_id": "default", "memory_context": ""}
    values = await graph.ainvoke(state, config={"configurable": {"thread_id": "t-prerw-gate"}})
    assert captured == ["BGE-M3输出的dense向量是多少维?"], "门控直通时retrieve应收到原始问题"
    assert values.get("source") == "kb_grounded"


# ---------- 2. 开关关闭: 不调LLM, query保持原问题 ----------


@pytest.mark.asyncio
async def test_kb_pre_rewrite_disabled_keeps_original_query(monkeypatch):
    import src.agents.graph as G
    import src.retrieval.reranker as RR
    from src.storage.protocol import RetrievedDocument

    captured: list[str] = []

    class FakeRetriever:
        async def retrieve(self, query, filters, top_k):
            captured.append(query)
            return [RetrievedDocument(chunk_id="c1", document_id="doc_kb", version_id="v1",
                                      parent_id=None, content="jieba基于前缀词典切分", locator={"page": 1},
                                      score=0.9, retrieval_source="rerank")]

    async def fake_rerank(query, docs, top_n=5):
        return docs

    async def must_not_chat(messages):
        raise AssertionError("开关关闭时不应有预改写LLM调用(chat()仅预改写使用)")

    async def fake_chat_stream(messages):
        yield "用jieba[1]"

    async def fake_chat_json(messages):
        content = messages[0]["content"]
        if "检查回答质量" in content:
            return {"grounded": True, "citations_valid": True, "enough_evidence": True, "issue": "ok"}
        return {"grades": [{"index": 1, "relevant": True}]}

    monkeypatch.setattr(RR, "rerank", fake_rerank)
    monkeypatch.setattr(G, "chat", must_not_chat)
    monkeypatch.setattr(G, "chat_stream", fake_chat_stream)
    monkeypatch.setattr(G, "chat_json", fake_chat_json)
    monkeypatch.setattr(G, "get_settings",
                        lambda: SimpleNamespace(retrieval_query_rewrite=False, retrieval_min_score=0.30))

    graph = G.build_graph(FakeRetriever())
    state = {"question": "那个分词的库咋用的来着?", "query": "那个分词的库咋用的来着?",
             "tenant_id": "default", "retry_count": 0, "documents": [], "search_decision": "",
             "generation": "", "verification": None, "conversation_history": [], "kb_ids": [],
             "intent": "", "source": "", "mode": "kb", "safety": "allow",
             "user_id": "default", "memory_context": ""}
    values = await graph.ainvoke(state, config={"configurable": {"thread_id": "t-prerw-2"}})
    assert captured == ["那个分词的库咋用的来着?"], "开关关闭时retrieve应收到原始问题"
    assert values.get("source") == "kb_grounded"


# ---------- 3. LLM失败兜底: 不阻塞, 用原问题继续检索 ----------


@pytest.mark.asyncio
async def test_kb_pre_rewrite_failure_falls_back(monkeypatch):
    import src.agents.graph as G
    import src.retrieval.reranker as RR
    from src.storage.protocol import RetrievedDocument

    captured: list[str] = []

    class FakeRetriever:
        async def retrieve(self, query, filters, top_k):
            captured.append(query)
            return [RetrievedDocument(chunk_id="c1", document_id="doc_kb", version_id="v1",
                                      parent_id=None, content="jieba基于前缀词典切分", locator={"page": 1},
                                      score=0.9, retrieval_source="rerank")]

    async def fake_rerank(query, docs, top_n=5):
        return docs

    async def broken_chat(messages):
        raise RuntimeError("LLM端点超时")

    async def fake_chat_stream(messages):
        yield "用jieba[1]"

    async def fake_chat_json(messages):
        content = messages[0]["content"]
        if "检查回答质量" in content:
            return {"grounded": True, "citations_valid": True, "enough_evidence": True, "issue": "ok"}
        return {"grades": [{"index": 1, "relevant": True}]}

    monkeypatch.setattr(RR, "rerank", fake_rerank)
    monkeypatch.setattr(G, "chat", broken_chat)
    monkeypatch.setattr(G, "chat_stream", fake_chat_stream)
    monkeypatch.setattr(G, "chat_json", fake_chat_json)

    graph = G.build_graph(FakeRetriever())
    state = {"question": "那个分词的库咋用的来着?", "query": "那个分词的库咋用的来着?",
             "tenant_id": "default", "retry_count": 0, "documents": [], "search_decision": "",
             "generation": "", "verification": None, "conversation_history": [], "kb_ids": [],
             "intent": "", "source": "", "mode": "kb", "safety": "allow",
             "user_id": "default", "memory_context": ""}
    values = await graph.ainvoke(state, config={"configurable": {"thread_id": "t-prerw-3"}})
    assert captured == ["那个分词的库咋用的来着?"], "预改写失败必须兜底原问题继续检索"
    assert values.get("source") == "kb_grounded"
