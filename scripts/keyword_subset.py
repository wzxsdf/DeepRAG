# 关键词8问子集对照: dense vs hybrid(BM25路) — BM25理论上应发挥的查询类型上谁赢
# 只读评测: 独占集合 + golden v3 尾部8问(ASCII精确token), 无LLM调用, ~1分钟
import asyncio
import os
import sys

from dotenv import load_dotenv

load_dotenv(".env")

from src.evaluation.ablation import _metrics, _to_documents, create_eval_writer, delete_eval_collection, ingest_eval_set
from src.evaluation.golden import GOLDEN
from src.retrieval.embedder import embed_query


async def main():
    keyword8 = GOLDEN[-8:]
    writer = create_eval_writer()
    try:
        await ingest_eval_set(writer)
        rows = {}
        for kind in ("dense", "hybrid"):
            agg = {"recall@1": 0.0, "recall@5": 0.0, "mrr@5": 0.0, "ndcg@5": 0.0}
            per = []
            for g in keyword8:
                dense = await embed_query(g["q"])
                if kind == "dense":
                    res = writer.client.query_points(writer.collection_name, query=dense, using="dense",
                                                     limit=5, with_payload=True)
                    docs = _to_documents(res.points)
                else:
                    pts = writer.query_hybrid(dense, g["q"], tenant_id="default", limit=5)
                    docs = _to_documents(pts)
                m = _metrics([d.document_id for d in docs], set(g["relevant"]))
                for k in agg:
                    agg[k] += m[k]
                per.append((g["q"][:28], round(m["recall@5"], 2)))
            rows[kind] = {k: v / len(keyword8) for k, v in agg.items()} | {"per": per}
        print("=" * 76)
        print("关键词8问子集(golden v3尾部, ASCII精确token) — dense vs hybrid(BM25)")
        print("=" * 76)
        for kind in ("dense", "hybrid"):
            r = rows[kind]
            print(f"{kind:8} R@1={r['recall@1']:.3f} R@5={r['recall@5']:.3f} "
                  f"MRR@5={r['mrr@5']:.3f} nDCG@5={r['ndcg@5']:.3f}")
        print("\n逐问 R@5 (dense / hybrid):")
        for (q, d), (_, h) in zip(rows["dense"]["per"], rows["hybrid"]["per"]):
            mark = "  dense赢" if d > h else ("  hybrid赢" if h > d else "")
            print(f"  {q:<30} {d:.2f} / {h:.2f}{mark}")
    finally:
        delete_eval_collection(writer)


if __name__ == "__main__":
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    asyncio.run(main())
