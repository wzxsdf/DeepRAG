# ponytail: 监控接口 - 入库job统计/文档chunk会话计数/依赖健康(用共享resources)
from fastapi import APIRouter, Depends

from src.storage.mysql import MySQLStore
from src.storage.qdrant_store import QdrantWriter
from src.storage.oss import OssStore
from src.security.auth import get_current_user
from src.core.lifespan import get_mysql, get_qdrant, get_oss

router = APIRouter(prefix="/api/v1", tags=["monitor"])


@router.get("/metrics")
async def metrics(user: dict = Depends(get_current_user),
                  mysql: MySQLStore = Depends(get_mysql),
                  qdrant: QdrantWriter = Depends(get_qdrant),
                  oss: OssStore = Depends(get_oss)):
    jobs, doc_count, chunk_count, conv_count, msg_count = {}, 0, 0, 0, 0
    try:
        for r in await mysql.fetch("SELECT status, COUNT(*) AS c FROM indexing_jobs GROUP BY status"):
            jobs[r["status"]] = r["c"]
        doc_count = (await mysql.fetchrow("SELECT COUNT(*) AS c FROM documents"))["c"]
        chunk_count = (await mysql.fetchrow("SELECT COUNT(*) AS c FROM chunks"))["c"]
        conv_count = (await mysql.fetchrow("SELECT COUNT(DISTINCT conversation_id) AS c FROM conversation_messages"))["c"]
        msg_count = (await mysql.fetchrow("SELECT COUNT(*) AS c FROM conversation_messages"))["c"]
    except Exception:
        pass  # ponytail: conversation_messages表可能未建(无对话时)

    deps = {"mysql": "ok", "qdrant": "unknown", "oss": "unknown"}
    try:
        qdrant.client.get_collection("chunks")
        deps["qdrant"] = "ok"
    except Exception:
        deps["qdrant"] = "error"
    try:
        oss.bucket.get_bucket_info()
        deps["oss"] = "ok"
    except Exception:
        deps["oss"] = "error"

    return {"jobs": jobs, "documents": doc_count, "chunks": chunk_count,
            "conversations": conv_count, "messages": msg_count, "dependencies": deps}
