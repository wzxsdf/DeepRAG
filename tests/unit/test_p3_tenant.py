# P3多租户单测: KB可见性判定 / 缓存键用户隔离 / Repository归属SQL / 强制鉴权语义
# 归属模型: owner_id NULL=公共库(legacy兼容), 非NULL=私有(仅owner可读写)

import pytest
from fastapi import HTTPException

from apps.api.access import ensure_kb_readable, ensure_kb_writable
from src.domain.models import KnowledgeBase


def _kb(owner_id):
    return KnowledgeBase(id="kb1", tenant_id="default", name="测试库", owner_id=owner_id)


# ---------- 1. 可见性判定(纯函数, kb/documents/query三处共用同一口径) ----------


def test_kb_readable_public_visible_to_all():
    assert ensure_kb_readable(_kb(None), "user_a").id == "kb1"  # 公共库任何登录用户可读


def test_kb_readable_owner_ok():
    assert ensure_kb_readable(_kb("user_a"), "user_a").id == "kb1"


def test_kb_readable_private_other_user_403():
    with pytest.raises(HTTPException) as ei:
        ensure_kb_readable(_kb("user_a"), "user_b")
    assert ei.value.status_code == 403


def test_kb_readable_missing_404():
    with pytest.raises(HTTPException) as ei:
        ensure_kb_readable(None, "user_a")
    assert ei.value.status_code == 404


def test_kb_writable_same_semantics():
    assert ensure_kb_writable(_kb("user_a"), "user_a").id == "kb1"
    with pytest.raises(HTTPException) as ei:
        ensure_kb_writable(_kb("user_a"), "user_b")
    assert ei.value.status_code == 403


# ---------- 2. 答案缓存键按用户隔离 ----------


def test_cache_key_differs_by_user_id():
    from src.cache.query_cache import QueryCache

    base = {"tenant_id": "default", "kb_ids": [], "mode": "kb"}
    k_a = QueryCache()._generate_cache_key("什么是RAG?", {**base, "user_id": "user_a"})
    k_b = QueryCache()._generate_cache_key("什么是RAG?", {**base, "user_id": "user_b"})
    assert k_a != k_b, "不同用户的同问题缓存键必须不同(答案缓存按用户隔离)"


# ---------- 3. Repository归属SQL ----------


class FakeMySQL:
    """记录SQL调用, 返回 canned 行(只够repository签名)."""

    def __init__(self, rows=None, row=None):
        self.calls: list[tuple] = []
        self._rows = rows or []
        self._row = row

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        return 1

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return self._rows

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return self._row

    async def execute_transaction(self, statements):
        self.calls.append(("tx", statements, ()))
        return []


@pytest.mark.asyncio
async def test_kb_create_persists_owner():
    from src.storage.repositories import KBRepository

    m = FakeMySQL()
    await KBRepository(m).create("kb1", "default", "私有库", owner_id="user_a")
    insert_sql = next(sql for _, sql, _ in m.calls if "INSERT INTO knowledge_bases" in sql)
    assert "owner_id" in insert_sql
    args = next(a for _, s, a in m.calls if "INSERT INTO knowledge_bases" in s)
    assert "user_a" in args


@pytest.mark.asyncio
async def test_kb_list_scopes_by_user():
    from src.storage.repositories import KBRepository

    m = FakeMySQL(rows=[])
    await KBRepository(m).list_with_doc_count(user_id="user_a")
    sql = m.calls[0][1]
    assert "owner_id IS NULL OR owner_id=%s" in sql.replace("kb.", "").replace("  ", " ") or \
           "owner_id IS NULL" in sql, "列表SQL必须含公共+本人私有过滤"
    args = m.calls[0][2]
    assert "user_a" in args


@pytest.mark.asyncio
async def test_kb_list_unscoped_without_user():
    from src.storage.repositories import KBRepository

    m = FakeMySQL(rows=[])
    await KBRepository(m).list_with_doc_count(user_id=None)
    assert "owner_id" not in m.calls[0][1], "监控/脚本口径(None)不过滤owner"


@pytest.mark.asyncio
async def test_doc_list_filters_inaccessible_private_kb():
    from src.storage.repositories import DocumentRepository

    m = FakeMySQL(rows=[])
    await DocumentRepository(m).list_active(None, accessible_owner="user_a")
    sql = m.calls[0][1]
    assert "IN (NULL, %s)" in sql, "文档列表必须经kb.owner_id过滤私有库"
    assert "user_a" in m.calls[0][2]


@pytest.mark.asyncio
async def test_load_history_filters_by_user():
    from src.storage.repositories import ConversationRepository

    m = FakeMySQL(rows=[])
    await ConversationRepository(m).load_history("conv1", user_id="user_a")
    sql = m.calls[0][1]
    assert "user_id=%s" in sql, "历史读取必须按归属过滤(防复用他人conversation_id读历史)"
    assert "user_a" in m.calls[0][2]
    await ConversationRepository(m).load_history("conv1")  # 旧口径(评测脚本)不受影响
    assert "user_id=" not in m.calls[1][1]


# ---------- 4. 强制鉴权: 无token→401(不再有"default"用户兜底) ----------


@pytest.mark.asyncio
async def test_get_current_user_requires_token():
    from src.security.auth import get_current_user

    with pytest.raises(HTTPException) as ei:
        await get_current_user(token=None)
    assert ei.value.status_code == 401


@pytest.mark.asyncio
async def test_get_current_user_rejects_bad_token():
    from src.security.auth import get_current_user

    with pytest.raises(HTTPException) as ei:
        await get_current_user(token="not-a-jwt")
    assert ei.value.status_code == 401
