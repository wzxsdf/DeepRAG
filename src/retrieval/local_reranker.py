# ponytail: 本地ONNX reranker(P0可靠性终局) — 确定性无跨窗漂移, 无API依赖
# 权重/导出: models/bge-reranker-v2-m3/model_quantized.onnx (scripts/export_reranker_onnx.py)
# 依赖: onnxruntime + transformers(tokenizer) 为 optional-dependencies[local-rerank], 默认安装不含

import threading
from pathlib import Path

from src.core.logging import log
from src.storage.protocol import RetrievedDocument

_MODEL_DIR = Path(__file__).resolve().parent.parent.parent / "models" / "bge-reranker-v2-m3"
_lock = threading.Lock()
_session = None  # (ort_session, tokenizer)


def _load():
    """懒加载 ONNX session + tokenizer(进程内单例, 首问加载~1s, 后续零开销)."""
    global _session
    with _lock:
        if _session is not None:
            return _session
        quant = _MODEL_DIR / "model_quantized.onnx"
        fp32 = _MODEL_DIR / "model.onnx"
        path = quant if quant.exists() else fp32
        if not path.exists():
            raise FileNotFoundError(
                f"本地reranker模型不存在: {path} — 先跑 scripts/export_reranker_onnx.py 导出")
        import onnxruntime as ort
        from transformers import AutoTokenizer

        sess = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        tok = AutoTokenizer.from_pretrained(str(_MODEL_DIR))
        _session = (sess, tok)
        log.info("local_reranker_loaded", model=str(path.name))
    return _session


async def rerank_local(query: str, docs: list[RetrievedDocument], top_n: int = 5) -> list[RetrievedDocument]:
    """本地Cross-Encoder精排: sigmoid(logit)对齐SiliconFlow的relevance_score口径(0~1).
    同步CPU推理(to_thread包裹不阻塞事件循环): 20对约100-300ms(int8量化)."""
    if not docs:
        return docs
    import asyncio
    return await asyncio.to_thread(_rerank_sync, query, docs, top_n)


def _rerank_sync(query: str, docs: list[RetrievedDocument], top_n: int) -> list[RetrievedDocument]:
    import numpy as np

    sess, tok = _load()
    pairs = [[query, d.content[:512]] for d in docs]  # 截512防长文档序列爆内存
    inputs = tok(pairs, padding=True, truncation=True, max_length=512, return_tensors="np")
    feed = {"input_ids": inputs["input_ids"].astype(np.int64),
            "attention_mask": inputs["attention_mask"].astype(np.int64)}
    logits = sess.run(["logits"], feed)[0][:, 0]
    scores = 1.0 / (1.0 + np.exp(-logits.astype(np.float64)))  # sigmoid对齐API口径
    ranked_docs = sorted(zip(docs, scores), key=lambda x: -x[1])[:top_n]
    out = []
    for d, s in ranked_docs:
        d.score = float(s)
        d.retrieval_source = "rerank_local"
        out.append(d)
    return out
