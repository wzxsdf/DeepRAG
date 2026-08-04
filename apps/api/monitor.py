# ponytail: 监控接口 - 入库job统计/文档chunk会话计数/依赖健康

import os
from fastapi import APIRouter, Depends
from dotenv import load_dotenv

from src.storage.mysql import MySQLStore
from src.security.auth import get_current_user

load_dotenv()
router = APIRouter(prefix="/api/v1", tags=["monitor"])


def _mysql():
    return MySQLStore(host=os.getenv("MYSQL_HOST", ""), port=int(os.getenv("MYSQL_PORT", "3306")),
                      db=os.getenv("MYSQL_DATABASE", os.getenv("MYSQL_DB", "deeprag")),
                      user=os.getenv("MYSQL_USER", ""), password=os.getenv("MYSQL_PASSWORD", ""))


@router.get("/metrics")
async def metrics(user: dict = Depends(get_current_user)):
    mysql = _mysql()
    jobs, doc_count, chunk_count, conv_count, msg_count = {}, 0, 0, 0, 0
    await mysql.connect()
    try:
        for r in await mysql.fetch("SELECT status, COUNT(*) AS c FROM indexing_jobs GROUP BY status"):
            jobs[r["status"]] = r["c"]
        doc_count = (await mysql.fetchrow("SELECT COUNT(*) AS c FROM documents"))["c"]
        chunk_count = (await mysql.fetchrow("SELECT COUNT(*) AS c FROM chunks"))["c"]
        conv_count = (await mysql.fetchrow("SELECT COUNT(DISTINCT conversation_id) AS c FROM conversation_messages"))["c"]
        msg_count = (await mysql.fetchrow("SELECT COUNT(*) AS c FROM conversation_messages"))["c"]
    except Exception:
        pass  # ponytail: conversation_messages表可能未建(无对话时)
    finally:
        await mysql.close()

    deps = {"mysql": "ok", "qdrant": "unknown", "oss": "unknown"}
    try:
        from src.storage.qdrant_store import QdrantWriter
        QdrantWriter(host=os.getenv("QDRANT_HOST", ""), port=int(os.getenv("QDRANT_PORT", "6333")),
                     api_key=os.getenv("QDRANT_API_KEY", "")).client.get_collection("chunks")
        deps["qdrant"] = "ok"
    except Exception:
        deps["qdrant"] = "error"
    try:
        from src.storage.oss import OssStore
        OssStore(endpoint=os.getenv("OSS_ENDPOINT", ""), bucket=os.getenv("OSS_BUCKET", ""),
                 access_key_id=os.getenv("OSS_ACCESS_KEY_ID", ""),
                 access_key_secret=os.getenv("OSS_ACCESS_KEY_SECRET", "")).bucket.get_bucket_info()
        deps["oss"] = "ok"
    except Exception:
        deps["oss"] = "error"

    return {"jobs": jobs, "documents": doc_count, "chunks": chunk_count,
            "conversations": conv_count, "messages": msg_count, "dependencies": deps}
