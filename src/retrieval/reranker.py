# ponytail: BGE-Reranker精排, SiliconFlow /rerank (已验证可用); 失败回退原序不阻塞

import os
import httpx
from typing import List
from dotenv import load_dotenv

from src.storage.protocol import RetrievedDocument
from src.core.logging import log

load_dotenv()

_RERANK_URL = os.getenv("EMBEDDING_BASE_URL", "") + "/rerank"
_RERANK_HEADERS = {"Authorization": f"Bearer {os.getenv('EMBEDDING_API_KEY', '')}"}
DEFAULT_RETRIEVAL_CANDIDATES = 20


def _rerank_model() -> str:
    """P0 A/B: 模型可配(env RERANK_MODEL), 分数分布随模型变 — 换模型必须重标定阈值(见config注释)."""
    from src.core.config import get_settings
    return get_settings().rerank_model


def _selfheal_min() -> float:
    from src.core.config import get_settings
    return get_settings().rerank_selfheal_min


def deduplicate_documents(docs: List[RetrievedDocument]) -> List[RetrievedDocument]:
    """按父块去重，保留融合排序中最靠前的 child."""
    unique: list[RetrievedDocument] = []
    seen: set[str] = set()
    for doc in docs:
        key = f"parent:{doc.parent_id}" if doc.parent_id else f"chunk:{doc.chunk_id}"
        if key in seen:
            continue
        seen.add(key)
        unique.append(doc)
    return unique


async def rerank(query: str, docs: List[RetrievedDocument], top_n: int = 5) -> List[RetrievedDocument]:
    """Cross-Encoder精排. 先按parent去重，失败时回退去重后的原序.
    后端可配(rerank_backend): api=SiliconFlow(默认) / local_onnx=本地int8量化(确定性无漂移).
    ponytail: 端点偶发"全零退化响应"(20261126晚间实测: 明显相关文档对返回0.0分, 干扰正常问答) —
    全低分(<selfheal_min)时重试一次取更优, 退化可自愈; 真不相关(不可答问题)重试后仍低分, 行为不变."""
    from src.core.config import get_settings

    docs = deduplicate_documents(docs)
    if not docs:
        return docs

    if get_settings().rerank_backend == "local_onnx":
        # 本地后端: 无网络退化问题, 自愈重试无意义(确定性), 失败仍回退原序不阻塞
        from src.retrieval.local_reranker import rerank_local

        try:
            return await rerank_local(query, docs, top_n)
        except Exception as e:
            log.warning("local_rerank 回退原序", error=type(e).__name__, msg=str(e))
            return docs[:top_n]

    def _make_call(model: str):
        async def _call() -> List[RetrievedDocument]:
            payload = {
                "model": model,
                "query": query,
                "documents": [d.content for d in docs],
                "top_n": min(top_n, len(docs)),
                "return_documents": False,
            }
            async with httpx.AsyncClient(timeout=60) as c:
                r = await c.post(_RERANK_URL, headers=_RERANK_HEADERS, json=payload)
            r.raise_for_status()
            ranked = []
            for item in r.json().get("results", []):
                doc = docs[item["index"]]
                doc.score = item["relevance_score"]
                doc.retrieval_source = "rerank"
                ranked.append(doc)
            return ranked
        return _call

    async def _apply_refuser_scores(ranked: List[RetrievedDocument]) -> bool:
        """双模型集成: refuser模型(bge)对同批top-N重打分并覆写score — 排序权留给排序模型,
        拒答门控回到bge分数分布(grade阈值0.30语义不变). 失败返回False(保留排序分, 降级为单模型).
        注意: 两个模型的排序可能不同, 必须按chunk_id映射覆写, 不能按位置zip."""
        try:
            rescored = await _make_call(get_settings().rerank_refuser_model)()
            if len(rescored) != len(ranked):
                raise ValueError(f"refuser返回数不齐: {len(rescored)} vs {len(ranked)}")
            score_by_id = {d.chunk_id: d.score for d in rescored}
            missing = 0
            for doc in ranked:
                if doc.chunk_id in score_by_id:
                    doc.score = score_by_id[doc.chunk_id]
                    doc.retrieval_source = "rerank_ensemble"
                else:
                    missing += 1
            if missing:
                raise ValueError(f"refuser未覆盖{missing}个chunk")
            return True
        except Exception as e:
            log.warning("rerank_ensemble_refuser_failed", error=type(e).__name__, msg=str(e))
            return False

    try:
        ranked = await _make_call(_rerank_model())()
        top1 = max((d.score for d in ranked), default=0.0)
        # ponytail: 触发线与grade阈值同线标定(可配rerank_selfheal_min) — 退化响应实测跨0~0.2分段
        # (20261127凌晨: 纯零重试后仍出现0.1x分段退化, 漏过旧触发线0.05, 致~10问被阈值误杀短路),
        # <触发线的可疑全低分都重试一次
        selfheal = _selfheal_min()
        if ranked and top1 < selfheal:
            log.warning("rerank_suspicious_low_retry", top1=round(top1, 4), n=len(ranked))
            retry = await _make_call(_rerank_model())()
            retry_top1 = max((d.score for d in retry), default=0.0)
            if retry_top1 > top1:
                ranked = retry
        if ranked and get_settings().rerank_ensemble_refuser:
            await _apply_refuser_scores(ranked)
        return ranked
    except Exception as e:
        # ponytail: rerank失败不阻塞主流程, 回退原序(已融合的RRF序)裁剪top_n
        log.warning("rerank 回退原序", error=type(e).__name__, msg=str(e))
        return docs[:top_n]
