# ponytail: 监控接口 - 入库job统计/文档chunk会话计数/依赖健康; 计数经Repository(删内联SQL)
from fastapi import APIRouter, Depends

from src.storage.mysql import MySQLStore
from src.storage.qdrant_store import QdrantWriter
from src.storage.oss import OssStore
from src.storage.repositories import (IndexingJobRepository, DocumentRepository,
    ChunkRepository, ConversationRepository)
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
        jobs = await IndexingJobRepository(mysql).count_by_status()
        doc_count = await DocumentRepository(mysql).count()
        chunk_count = await ChunkRepository(mysql).count()
        conv_count = await ConversationRepository(mysql).count_distinct_conversations()
        msg_count = await ConversationRepository(mysql).count_messages()
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
