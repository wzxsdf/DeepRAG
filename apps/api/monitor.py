# ponytail: 监控接口 - 入库job统计/文档chunk会话计数/依赖健康; 计数经Repository(删内联SQL)
from fastapi import APIRouter, Depends

from src.storage.mysql import MySQLStore
from src.storage.qdrant_store import QdrantWriter
from src.storage.oss import OssStore
from src.storage.repositories import (IndexingJobRepository, DocumentRepository,
    ChunkRepository, ConversationRepository)
from src.security.auth import get_current_user
from src.core.lifespan import get_mysql, get_qdrant, get_oss, get_consolidator, get_long_term_store, get_fact_store, get_memory_queue

router = APIRouter(prefix="/api/v1", tags=["monitor"])


@router.get("/metrics")
async def metrics(user: dict = Depends(get_current_user),
                  mysql: MySQLStore = Depends(get_mysql),
                  qdrant: QdrantWriter = Depends(get_qdrant),
                  oss: OssStore = Depends(get_oss),
                  long_term_store=Depends(get_long_term_store),
                  fact_store=Depends(get_fact_store),
                  memory_queue=Depends(get_memory_queue)):
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

    # F1: 记忆库健康指标(各store未启用则对应字段缺省)
    memory = {}
    try:
        if long_term_store is not None:
            memory["agent_memory_total"] = await long_term_store.count()
        if fact_store is not None:
            memory["user_facts_total"] = await fact_store.count()
        if memory_queue is not None:
            memory["queue_pending"] = await memory_queue.count_pending()
    except Exception:
        pass

    return {"jobs": jobs, "documents": doc_count, "chunks": chunk_count,
            "conversations": conv_count, "messages": msg_count, "dependencies": deps,
            "memory": memory}


@router.post("/memory/consolidate")
async def consolidate_memory(user: dict = Depends(get_current_user),
                             user_id: str | None = None,
                             consolidator=Depends(get_consolidator)):
    """P3 手动触发记忆整合(去重+抽象提炼)。

    - 不传 user_id: 全量整合所有用户
    - 传 user_id: 仅整合指定用户
    返回整合统计。consolidator 未启用(P1关闭)时返回 skipped。
    """
    if consolidator is None:
        return {"skipped": True, "reason": "memory consolidator disabled (P1 long_term off)"}
    if user_id:
        stats = await consolidator.consolidate_user(user_id)
    else:
        stats = await consolidator.consolidate_all()
    return {"status": "ok", "stats": stats}


@router.get("/memory/list")
async def list_memory(user: dict = Depends(get_current_user),
                      user_id: str = "default",
                      memory_type: str | None = None,
                      long_term_store=Depends(get_long_term_store)):
    """F2: 查看某用户的长期记忆(人工核查/调试)。"""
    if long_term_store is None:
        return {"skipped": True, "reason": "long_term store disabled"}
    items = await long_term_store.list_all(user_id, memory_type=memory_type or None)
    return {"user_id": user_id, "count": len(items), "memories": items}


@router.post("/memory/update")
async def update_memory(user: dict = Depends(get_current_user),
                        memory_id: str = "",
                        content: str | None = None,
                        importance: float | None = None,
                        long_term_store=Depends(get_long_term_store)):
    """F2: 编辑记忆(人工修正)。content 改会重 embed; importance 调重要性。

    consolidator 整合时可据此修正 LLM 抽取错误。
    """
    if long_term_store is None:
        return {"skipped": True, "reason": "long_term store disabled"}
    if not memory_id:
        return {"status": "error", "reason": "memory_id required"}
    ok = await long_term_store.update(memory_id, content=content, importance=importance)
    return {"status": "ok" if ok else "error", "memory_id": memory_id}


@router.delete("/memory/{memory_id}")
async def delete_memory(memory_id: str,
                        user: dict = Depends(get_current_user),
                        long_term_store=Depends(get_long_term_store)):
    """F2: 删除单条记忆(过时/错误/隐私)。"""
    if long_term_store is None:
        return {"skipped": True, "reason": "long_term store disabled"}
    await long_term_store.delete_one(memory_id)
    return {"status": "ok", "deleted": memory_id}


@router.get("/memory/consolidate/status")
async def consolidate_status(user: dict = Depends(get_current_user),
                             consolidator=Depends(get_consolidator)):
    """F3: 整合器运行状态(跑没跑/耗时/上次结果)。

    返回 {running, last_run_at, last_stats, run_count, last_error}。后台整合可观测。
    """
    if consolidator is None:
        return {"skipped": True, "reason": "memory consolidator disabled"}
    return {"status": "ok", "consolidator": consolidator.get_status()}
