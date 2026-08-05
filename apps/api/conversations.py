# ponytail: 会话历史接口(按user隔离)
from fastapi import APIRouter, Depends

from src.storage.mysql import MySQLStore
from src.security.auth import get_current_user
from src.core.lifespan import get_mysql
from apps.api.query import _ensure_conv_table

router = APIRouter(prefix="/api/v1", tags=["conversations"])


@router.get("/conversations")
async def list_conversations(user: dict = Depends(get_current_user), mysql: MySQLStore = Depends(get_mysql)):
    """当前用户的会话列表(按最近活动倒序)."""
    await _ensure_conv_table(mysql)
    rows = await mysql.fetch("""
        SELECT conversation_id, MIN(content) AS first_msg, MAX(created_at) AS last_at, COUNT(*) AS msg_cnt
        FROM conversation_messages WHERE user_id = %s GROUP BY conversation_id ORDER BY last_at DESC
    """, user["user_id"])
    return [{"id": r["conversation_id"], "title": (r["first_msg"] or "")[:40],
             "messages": r["msg_cnt"], "updated_at": str(r["last_at"])} for r in rows]


@router.get("/conversations/{conv_id}/messages")
async def list_messages(conv_id: str, user: dict = Depends(get_current_user), mysql: MySQLStore = Depends(get_mysql)):
    """某会话的消息列表(校验归属)."""
    await _ensure_conv_table(mysql)
    rows = await mysql.fetch(
        "SELECT role, content, created_at FROM conversation_messages "
        "WHERE conversation_id = %s AND user_id = %s ORDER BY id", conv_id, user["user_id"])
    return [{"role": r["role"], "content": r["content"], "created_at": str(r["created_at"])} for r in rows]
