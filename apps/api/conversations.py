# ponytail: 会话历史接口(按user隔离)

import os
from fastapi import APIRouter, Depends
from dotenv import load_dotenv

from src.storage.mysql import MySQLStore
from src.security.auth import get_current_user
from apps.api.query import _ensure_conv_table

load_dotenv()
router = APIRouter(prefix="/api/v1", tags=["conversations"])


def _mysql():
    return MySQLStore(host=os.getenv("MYSQL_HOST", ""), port=int(os.getenv("MYSQL_PORT", "3306")),
                      db=os.getenv("MYSQL_DATABASE", os.getenv("MYSQL_DB", "deeprag")),
                      user=os.getenv("MYSQL_USER", ""), password=os.getenv("MYSQL_PASSWORD", ""))


@router.get("/conversations")
async def list_conversations(user: dict = Depends(get_current_user)):
    """当前用户的会话列表(按最近活动倒序)."""
    mysql = _mysql()
    await mysql.connect()
    try:
        await _ensure_conv_table(mysql)
        rows = await mysql.fetch("""
            SELECT conversation_id, MIN(content) AS first_msg, MAX(created_at) AS last_at, COUNT(*) AS msg_cnt
            FROM conversation_messages WHERE user_id = %s GROUP BY conversation_id ORDER BY last_at DESC
        """, user["user_id"])
    finally:
        await mysql.close()
    return [{"id": r["conversation_id"], "title": (r["first_msg"] or "")[:40],
             "messages": r["msg_cnt"], "updated_at": str(r["last_at"])} for r in rows]


@router.get("/conversations/{conv_id}/messages")
async def list_messages(conv_id: str, user: dict = Depends(get_current_user)):
    """某会话的消息列表(校验归属)."""
    mysql = _mysql()
    await mysql.connect()
    try:
        await _ensure_conv_table(mysql)
        rows = await mysql.fetch(
            "SELECT role, content, created_at FROM conversation_messages "
            "WHERE conversation_id = %s AND user_id = %s ORDER BY id", conv_id, user["user_id"])
    finally:
        await mysql.close()
    return [{"role": r["role"], "content": r["content"], "created_at": str(r["created_at"])} for r in rows]
