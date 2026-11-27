# P3多租户: KB可见性判定 — 归属模型 owner_id: NULL=公共库(legacy兼容, 登录用户均可见), 非NULL=私有(仅owner)
# 判定逻辑收敛为纯函数(路由层消费), 保证kb/documents/query三处口径一致
from fastapi import HTTPException

from src.domain.models import KnowledgeBase


def ensure_kb_readable(kb: KnowledgeBase | None, user_id: str) -> KnowledgeBase:
    """校验用户对KB的读权限: 不存在→404; 私有库非owner→403; 公共库或owner→放行."""
    if kb is None:
        raise HTTPException(404, "知识库不存在")
    if kb.owner_id and kb.owner_id != user_id:
        raise HTTPException(403, "无权访问该知识库")
    return kb


def ensure_kb_writable(kb: KnowledgeBase | None, user_id: str) -> KnowledgeBase:
    """校验用户对KB的写权限(改名/删除/上传): 私有库仅owner; 公共库(legacy)维持登录即可写(与旧行为一致)."""
    return ensure_kb_readable(kb, user_id)
