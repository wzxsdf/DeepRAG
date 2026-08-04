# W3.2 父块展开验证: 入库同parent的children + 父块落OSS -> retrieve返回parent_content

import asyncio, os, sys, uuid
from dotenv import load_dotenv

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()

from src.storage.qdrant_store import QdrantWriter
from src.storage.oss import OssStore
from src.retrieval.embedder import embed_query
from src.retrieval.hybrid import HybridRetriever
from src.storage.protocol import RetrievalFilter

PARENT_TEXT = "DeepRAG是一个Agentic RAG系统,整体架构包含入库流水线、混合检索层和CRAG Agent状态机三大部分。"
CHILDREN = [
    ("c1", "DeepRAG是一个Agentic RAG系统。"),
    ("c2", "它的检索层用BGE-M3和BM25做混合检索。"),
    ("c3", "Agent用LangGraph实现CRAG自纠循环。"),
]
QUERY = "DeepRAG的整体架构包含什么?"


async def main():
    print("=" * 60)
    print("DeepRAG W3.2 父块展开验证")
    print("=" * 60)

    oss = OssStore(endpoint=os.getenv("OSS_ENDPOINT"), bucket=os.getenv("OSS_BUCKET"),
                   access_key_id=os.getenv("OSS_ACCESS_KEY_ID"), access_key_secret=os.getenv("OSS_ACCESS_KEY_SECRET"))
    writer = QdrantWriter(host=os.getenv("QDRANT_HOST"), port=int(os.getenv("QDRANT_PORT")), api_key=os.getenv("QDRANT_API_KEY", ""))
    writer.recreate_collection()

    # 父块落OSS (parent_id=p1)
    await oss.put("parent_chunks/p1.txt", PARENT_TEXT.encode())

    # 入库 children (共享 parent_id=p1, payload含parent_id)
    chunks = []
    for cid, content in CHILDREN:
        dense = await embed_query(content)
        chunks.append({"id": str(uuid.uuid5(uuid.NAMESPACE_DNS, cid)), "dense_vector": dense, "sparse_text": content,
                       "payload": {"tenant_id": "default", "document_id": "doc1", "version_id": "v1", "parent_id": "p1",
                                   "page": 1, "content": content}})
    await writer.insert_chunks(chunks)
    print(f"[1] 入库 {len(chunks)} child (parent_id=p1) + 父块落OSS")

    retriever = HybridRetriever()
    docs = await retriever.retrieve(QUERY, RetrievalFilter(tenant_id="default"), top_k=3)
    print(f"[2] 检索返回 {len(docs)} 条, 检查 parent_content:")
    for d in docs:
        status = f"有({len(d.parent_content)}字符)" if d.parent_content else "无"
        print(f"  child={d.chunk_id[:8]}..  content={d.content[:24]}..  parent_content={status}")
        if d.parent_content:
            print(f"    父块预览: {d.parent_content[:60]}...")

    ok = any(d.parent_content for d in docs)
    print(f"\n{'✓ 父块展开验证通过 (child命中+parent上下文返回)' if ok else '✗ parent_content 为空'}")
    return ok


if __name__ == "__main__":
    sys.exit(0 if asyncio.run(main()) else 1)
