# 架构优化③: DB索引幂等补齐单测 — SHOW INDEX探测→CREATE INDEX, 已存在跳过, 失败告警不抛
import pytest

from src.storage.repositories import DocumentRepository, KBRepository, _ensure_index


class FakeMySQL:
    """记录SQL调用; fetch返回canned行(fetchrow走同一入口简写为rows[0]语义, 这里只测索引逻辑)."""

    def __init__(self, index_exists: bool = False):
        self.calls: list[tuple] = []
        self._index_exists = index_exists
        self.fail_create = False

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))
        if self.fail_create and "CREATE INDEX" in sql:
            raise RuntimeError("permission denied")
        return 1

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        if "SHOW INDEX" in sql:
            return [{"Key_name": "idx_x"}] if self._index_exists else []
        return []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return None


@pytest.mark.asyncio
async def test_ensure_index_creates_when_missing():
    m = FakeMySQL(index_exists=False)
    await _ensure_index(m, "documents", "idx_doc_kb", "kb_id")
    creates = [sql for _, sql, _ in m.calls if "CREATE INDEX" in sql]
    assert creates == ["CREATE INDEX idx_doc_kb ON documents (kb_id)"]


@pytest.mark.asyncio
async def test_ensure_index_skips_when_exists():
    m = FakeMySQL(index_exists=True)
    await _ensure_index(m, "documents", "idx_doc_kb", "kb_id")
    assert not any("CREATE INDEX" in sql for _, sql, _ in m.calls), "已存在的索引不得重复创建"


@pytest.mark.asyncio
async def test_ensure_index_failure_does_not_raise():
    """索引创建失败(权限/竞态)只影响性能不影响正确性 — 必须吞异常保启动."""
    m = FakeMySQL(index_exists=False)
    m.fail_create = True
    await _ensure_index(m, "documents", "idx_doc_kb", "kb_id")  # 不抛即通过


@pytest.mark.asyncio
async def test_doc_repo_ensure_schema_creates_all_four_indexes():
    m = FakeMySQL(index_exists=False)
    await DocumentRepository(m).ensure_schema()
    created = [sql for _, sql, _ in m.calls if "CREATE INDEX" in sql]
    assert len(created) == 4, f"应建4个索引(documents×2/versions/chunks): {created}"
    assert any("idx_chunk_version" in s for s in created)
    assert any("idx_dv_document" in s for s in created)


@pytest.mark.asyncio
async def test_kb_repo_ensure_schema_creates_owner_and_deleted_indexes():
    m = FakeMySQL(index_exists=False)
    # ensure_schema 里还有 SHOW COLUMNS(返回空→补owner_id列) — fetch对SHOW COLUMNS也返回[]
    await KBRepository(m).ensure_schema()
    created = [sql for _, sql, _ in m.calls if "CREATE INDEX" in sql]
    assert any("idx_kb_owner" in s for s in created)
    assert any("idx_kb_deleted" in s for s in created)
