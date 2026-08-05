# ponytail: 会话历史接口(按user隔离) — 经ConversationRepository; 建表收敛到lifespan ensure_schema
from fastapi import APIRouter, Depends

from src.storage.mysql import MySQLStore
from src.storage.repositories import ConversationRepository
from src.security.auth import get_current_user
from src.core.lifespan import get_mysql

router = APIRouter(prefix="/api/v1", tags=["conversations"])


@router.get("/conversations")
async def list_conversations(user: dict = Depends(get_current_user), mysql: MySQLStore = Depends(get_mysql)):
    """当前用户的会话列表(按最近活动倒序)."""
    convs = await ConversationRepository(mysql).list_conversations(user["user_id"])
    return [{"id": c.id, "title": (c.title or "")[:40], "messages": c.messages, "updated_at": str(c.updated_at)}
            for c in convs]


@router.get("/conversations/{conv_id}/messages")
async def list_messages(conv_id: str, user: dict = Depends(get_current_user), mysql: MySQLStore = Depends(get_mysql)):
    """某会话的消息列表(校验归属)."""
    msgs = await ConversationRepository(mysql).get_messages(conv_id, user["user_id"])
    return [{"role": m.role, "content": m.content, "created_at": str(m.created_at)} for m in msgs]
