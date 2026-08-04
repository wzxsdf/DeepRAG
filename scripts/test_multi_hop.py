# W3.4 Planner多跳验证: complex对比问题 -> route分解子查询 -> 并行检索融合多文档

import asyncio, os, sys, uuid
from dotenv import load_dotenv

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

from src.storage.qdrant_store import QdrantWriter
from src.retrieval.embedder import embed_query
from src.retrieval.hybrid import HybridRetriever
from src.agents.graph import build_graph

DOCS = [
    ("doc_lg", "LangGraph是LangChain的图编排框架,擅长构建有状态的多步Agent,支持checkpoint持久化和人在环路中断。"),
    ("doc_li", "LlamaIndex是数据框架,擅长数据接入与索引,提供向量检索、知识图谱、树状检索等多种检索器和查询引擎。"),
    ("doc_paris", "巴黎是法国首都,有埃菲尔铁塔和卢浮宫。"),
]
QUESTION = "对比LangGraph和LlamaIndex两个框架各自擅长什么?"  # complex


async def main():
    print("=" * 60)
    print("DeepRAG W3.4 Planner 多跳验证")
    print("=" * 60)

    writer = QdrantWriter(host=os.getenv("QDRANT_HOST"), port=int(os.getenv("QDRANT_PORT")), api_key=os.getenv("QDRANT_API_KEY", ""))
    writer.recreate_collection()
    chunks = []
    for did, content in DOCS:
        dense = await embed_query(content)
        chunks.append({"id": str(uuid.uuid5(uuid.NAMESPACE_DNS, did)), "dense_vector": dense, "sparse_text": content,
                       "payload": {"tenant_id": "default", "document_id": did, "content": content}})
    await writer.insert_chunks(chunks)
    print(f"[1] 入库 {len(chunks)} 文档 (2框架对比 + 1无关)")

    graph = build_graph(HybridRetriever())
    cfg = {"configurable": {"thread_id": "mh-1"}}
    trace = []
    async for event in graph.astream(
        {"question": QUESTION, "query": QUESTION, "tenant_id": "default", "retry_count": 0,
         "documents": [], "search_decision": "", "generation": "", "verification": None,
         "conversation_history": [], "complexity": "", "sub_queries": []},
        config=cfg, stream_mode="updates",
    ):
        for node, update in event.items():
            trace.append(node)
            if node == "route":
                print(f"  >> route: complexity={update.get('complexity')} sub_queries={update.get('sub_queries')}")
            else:
                print(f"  >> {node}: 更新 {list(update.keys())}")

    values = (await graph.aget_state(config=cfg)).values
    doc_ids = [d.document_id for d in values.get("documents", [])]
    print(f"\n[2] 轨迹: {' → '.join(trace)}")
    print(f"    complexity={values.get('complexity')} | 命中文档: {doc_ids}")
    print(f"\n[3] 回答:\n{values.get('generation', '')}")

    complex_ok = values.get("complexity") == "complex" and len(values.get("sub_queries", [])) >= 2
    multi_doc = sum(1 for d in doc_ids if d in ("doc_lg", "doc_li")) >= 2
    print(f"\nroute判complex+分解子查询: {'✓' if complex_ok else '✗'}")
    print(f"多框架文档融合: {'✓' if multi_doc else '✗'}")
    print(f"{'✓ 多跳验证通过' if complex_ok and multi_doc else '✗ 未达预期'}")
    return complex_ok and multi_doc


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
