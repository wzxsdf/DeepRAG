# ponytail: BGE-Reranker精排, SiliconFlow /rerank (已验证可用); 失败回退原序不阻塞

import os
import httpx
from typing import List
from dotenv import load_dotenv

from src.storage.protocol import RetrievedDocument

load_dotenv()

_RERANK_URL = os.getenv("EMBEDDING_BASE_URL", "") + "/rerank"
_RERANK_HEADERS = {"Authorization": f"Bearer {os.getenv('EMBEDDING_API_KEY', '')}"}
_RERANK_MODEL = "BAAI/bge-reranker-v2-m3"


async def rerank(query: str, docs: List[RetrievedDocument], top_n: int = 5) -> List[RetrievedDocument]:
    """Cross-Encoder精排. 返回重排序docs(原地改score+source). 失败回退原序裁剪."""
    if not docs:
        return docs
    payload = {
        "model": _RERANK_MODEL,
        "query": query,
        "documents": [d.content for d in docs],
        "top_n": min(top_n, len(docs)),
        "return_documents": False,
    }
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(_RERANK_URL, headers=_RERANK_HEADERS, json=payload)
        r.raise_for_status()
        results = r.json().get("results", [])
        ranked = []
        for item in results:
            doc = docs[item["index"]]
            doc.score = item["relevance_score"]
            doc.retrieval_source = "rerank"
            ranked.append(doc)
        return ranked
    except Exception as e:
        # ponytail: rerank失败不阻塞主流程, 回退原序(已融合的RRF序)裁剪top_n
        print(f"[rerank] 回退原序: {type(e).__name__}: {e}")
        return docs[:top_n]
