# ponytail: Repository层 — 手写SQL(不用ORM), 返领域模型, 按实体分; 接MySQLStore执行层
# 收敛: API层内联SQL + worker内联UPDATE + 运行时DDL(ensure_schema); MySQLStore退化为纯执行层
# 编排(purge/delete_kb跨存储)留模块函数(不抽Service, YAGNI), 只下沉SQL到此

from src.storage.mysql import MySQLStore
from src.domain.models import (User, KnowledgeBase, Document, DocumentVersion, IndexingJob,
    ChunkMeta, ConversationMessage, KBWithCount, DocumentListItem, DocumentDetail, ConversationSummary)


class UserRepository:
    def __init__(self, mysql: MySQLStore):
        self._m = mysql

    async def ensure_schema(self):
        """收敛运行时DDL: seed default tenant + users补name/password_hash列(幂等). 替代auth._ensure_users_schema."""
        await self._m.execute("INSERT IGNORE INTO tenants (id, name) VALUES ('default', 'Default Tenant')")
        cols = {r["Field"] for r in await self._m.fetch("SHOW COLUMNS FROM users")}
        if "name" not in cols:
            await self._m.execute("ALTER TABLE users ADD COLUMN name VARCHAR(100) DEFAULT ''")
        if "password_hash" not in cols:
            await self._m.execute("ALTER TABLE users ADD COLUMN password_hash VARCHAR(100)")

    async def find_by_email(self, email: str) -> User | None:
        r = await self._m.fetchrow(
            "SELECT id, tenant_id, email, name, password_hash FROM users WHERE email=%s", email)
        return User(**r) if r else None

    async def create(self, id: str, tenant_id: str, email: str, name: str, password_hash: str):
        await self._m.execute(
            "INSERT INTO users (id, tenant_id, email, name, password_hash) VALUES (%s,%s,%s,%s,%s)",
            id, tenant_id, email, name, password_hash)


class KBRepository:
    def __init__(self, mysql: MySQLStore):
        self._m = mysql

    async def create(self, id: str, tenant_id: str, name: str):
        await self._m.execute("INSERT IGNORE INTO tenants (id, name) VALUES ('default','Default')")
        await self._m.execute("INSERT INTO knowledge_bases (id, tenant_id, name) VALUES (%s,%s,%s)",
                             id, tenant_id, name)

    async def list_with_doc_count(self) -> list[KBWithCount]:
        rows = await self._m.fetch(
            "SELECT kb.id, kb.name, kb.created_at, COUNT(d.id) AS doc_count "
            "FROM knowledge_bases kb LEFT JOIN documents d "
            "ON d.kb_id=kb.id AND d.deleted_at IS NULL "
            "WHERE kb.deleted_at IS NULL GROUP BY kb.id,kb.name,kb.created_at ORDER BY kb.created_at DESC")
        return [KBWithCount(id=r["id"], name=r["name"], doc_count=r["doc_count"], created_at=r["created_at"])
                for r in rows]

    async def get(self, kb_id: str) -> KnowledgeBase | None:
        r = await self._m.fetchrow("SELECT * FROM knowledge_bases WHERE id=%s AND deleted_at IS NULL", kb_id)
        if not r:
            return None
        return KnowledgeBase(id=r["id"], tenant_id=r["tenant_id"], name=r["name"], status=r.get("status", "ACTIVE"),
                             deleted_at=r.get("deleted_at"), deleted_by=r.get("deleted_by"), created_at=r.get("created_at"))

    async def find_including_deleted(self, kb_id: str) -> dict | None:
        # 幂等检查: 故意不过滤deleted_at(已删→返回deleted_at非空, 调用方据此判幂等)
        return await self._m.fetchrow("SELECT id, deleted_at FROM knowledge_bases WHERE id=%s", kb_id)

    async def rename(self, kb_id: str, name: str):
        await self._m.execute("UPDATE knowledge_bases SET name=%s WHERE id=%s", name, kb_id)

    async def set_status(self, kb_id: str, status: str):
        await self._m.execute("UPDATE knowledge_bases SET status=%s WHERE id=%s", status, kb_id)

    async def list_document_ids(self, kb_id: str) -> list[str]:
        rows = await self._m.fetch("SELECT id FROM documents WHERE kb_id=%s AND deleted_at IS NULL", kb_id)
        return [r["id"] for r in rows]

    async def soft_delete(self, kb_id: str, user_id: str = "default"):
        await self._m.execute_transaction([
            ("UPDATE knowledge_bases SET deleted_at=NOW(), deleted_by=%s, status='DELETED' "
             "WHERE id=%s AND deleted_at IS NULL", (user_id, kb_id)),
        ])


class DocumentRepository:
    def __init__(self, mysql: MySQLStore):
        self._m = mysql

    async def create(self, id: str, kb_id: str, title: str):
        await self._m.execute("INSERT IGNORE INTO knowledge_bases (id, tenant_id, name) VALUES ('default','default','Default KB')")
        await self._m.execute("INSERT INTO documents (id, kb_id, title) VALUES (%s,%s,%s)", id, kb_id, title)

    async def list_active(self, kb_id: str | None = None) -> list[DocumentListItem]:
        sql = ("SELECT d.id, d.title, d.kb_id, dv.status AS version_status, j.stage, j.status AS job_status, d.created_at "
               "FROM documents d LEFT JOIN document_versions dv ON dv.document_id=d.id "
               "LEFT JOIN indexing_jobs j ON j.version_id=dv.id WHERE d.deleted_at IS NULL")
        args: tuple = ()
        if kb_id:
            sql += " AND d.kb_id=%s"
            args = (kb_id,)
        sql += " ORDER BY d.created_at DESC LIMIT 100"
        rows = await self._m.fetch(sql, *args)
        return [DocumentListItem(id=r["id"], title=r["title"], kb_id=r["kb_id"],
                                status=r.get("version_status") or r.get("job_status") or "UNKNOWN",
                                stage=r.get("stage"), created_at=r.get("created_at")) for r in rows]

    async def get_detail(self, doc_id: str) -> DocumentDetail | None:
        r = await self._m.fetchrow(
            "SELECT d.id, d.title, dv.status AS version_status, dv.id AS version_id, j.stage, j.status AS job_status, j.error_msg "
            "FROM documents d LEFT JOIN document_versions dv ON dv.document_id=d.id "
            "LEFT JOIN indexing_jobs j ON j.version_id=dv.id WHERE d.id=%s AND d.deleted_at IS NULL", doc_id)
        if not r:
            return None
        return DocumentDetail(document_id=r["id"], title=r["title"], version_id=r.get("version_id"),
                             status=r.get("version_status") or r.get("job_status") or "UNKNOWN",
                             stage=r.get("stage"), error_msg=r.get("error_msg"))

    async def find_for_delete(self, doc_id: str) -> dict | None:
        # 幂等检查: 返回{deleted_at}行(NULL=未删/时间戳=已删)或None(记录不存在), 调用方据此区分404/幂等/删除
        return await self._m.fetchrow("SELECT deleted_at FROM documents WHERE id=%s", doc_id)

    async def purge_with_versions(self, doc_id: str, version_ids: list[str], user_id: str = "default"):
        """事务级联: 物理删 chunks/jobs/versions + 软删 document. Qdrant/OSS由调用方事务外删(幂等).
        替代MySQLStore.delete_document_data."""
        soft = ("UPDATE documents SET deleted_at=NOW(), deleted_by=%s WHERE id=%s AND deleted_at IS NULL",
                (user_id, doc_id))
        if not version_ids:
            await self._m.execute_transaction([soft])
            return
        ph = ",".join(["%s"] * len(version_ids))
        await self._m.execute_transaction([
            (f"DELETE FROM chunks WHERE version_id IN ({ph})", tuple(version_ids)),
            (f"DELETE FROM indexing_jobs WHERE version_id IN ({ph})", tuple(version_ids)),
            ("DELETE FROM document_versions WHERE document_id=%s", (doc_id,)),
            soft,
        ])

    async def count(self) -> int:
        r = await self._m.fetchrow("SELECT COUNT(*) AS c FROM documents")
        return (r or {}).get("c", 0)


class DocumentVersionRepository:
    def __init__(self, mysql: MySQLStore):
        self._m = mysql

    async def create(self, version_id: str, document_id: str, oss_key: str, file_size: int, sha256: str):
        await self._m.execute(
            "INSERT INTO document_versions (id, document_id, oss_key, file_size, sha256, status) "
            "VALUES (%s,%s,%s,%s,%s,'UPLOADED')", version_id, document_id, oss_key, file_size, sha256)

    async def get_ids(self, document_id: str) -> list[str]:
        rows = await self._m.fetch("SELECT id FROM document_versions WHERE document_id=%s", document_id)
        return [r["id"] for r in rows]

    async def get_oss_keys(self, document_id: str) -> list[str]:
        rows = await self._m.fetch("SELECT oss_key FROM document_versions WHERE document_id=%s", document_id)
        return [r["oss_key"] for r in rows if r.get("oss_key")]

    async def latest_oss_key(self, document_id: str) -> str | None:
        r = await self._m.fetchrow(
            "SELECT oss_key FROM document_versions WHERE document_id=%s ORDER BY created_at DESC LIMIT 1", document_id)
        return (r or {}).get("oss_key")

    async def update_status(self, version_id: str, status: str):
        await self._m.execute("UPDATE document_versions SET status=%s WHERE id=%s", status, version_id)


class IndexingJobRepository:
    def __init__(self, mysql: MySQLStore):
        self._m = mysql

    async def create(self, job_id: str, version_id: str):
        await self._m.execute(
            "INSERT INTO indexing_jobs (id, version_id, stage, status) VALUES (%s,%s,'PARSING','PENDING')",
            job_id, version_id)

    async def update(self, job_id: str, status: str, stage: str | None = None, error_msg: str | None = None):
        if error_msg:
            await self._m.execute(
                "UPDATE indexing_jobs SET status=%s, stage=%s, error_msg=%s, updated_at=NOW() WHERE id=%s",
                status, stage, error_msg, job_id)
        else:
            await self._m.execute(
                "UPDATE indexing_jobs SET status=%s, stage=%s, updated_at=NOW() WHERE id=%s", status, stage, job_id)

    async def update_by_version(self, version_id: str, status: str, error_msg: str | None = None):
        # ponytail: worker ingestion.py 用(按version_id更新, 非job_id)
        if error_msg:
            await self._m.execute("UPDATE indexing_jobs SET status=%s, error_msg=%s WHERE version_id=%s",
                                 status, error_msg, version_id)
        else:
            await self._m.execute("UPDATE indexing_jobs SET status=%s WHERE version_id=%s", status, version_id)

    async def count_by_status(self) -> dict[str, int]:
        rows = await self._m.fetch("SELECT status, COUNT(*) AS c FROM indexing_jobs GROUP BY status")
        return {r["status"]: r["c"] for r in rows}


class ChunkRepository:
    def __init__(self, mysql: MySQLStore):
        self._m = mysql

    async def insert(self, chunk_id: str, version_id: str, parent_id: str, page: int,
                     heading_path: str, content_hash: str, oss_key: str):
        await self._m.execute(
            "INSERT INTO chunks (id, version_id, parent_id, page, heading_path, content_hash, oss_key) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)", chunk_id, version_id, parent_id, page, heading_path, content_hash, oss_key)

    async def get_parent_ids(self, version_ids: list[str]) -> list[str]:
        if not version_ids:
            return []
        ph = ",".join(["%s"] * len(version_ids))
        rows = await self._m.fetch(
            f"SELECT DISTINCT parent_id FROM chunks WHERE version_id IN ({ph}) AND parent_id IS NOT NULL", *version_ids)
        return [r["parent_id"] for r in rows]

    async def count(self) -> int:
        r = await self._m.fetchrow("SELECT COUNT(*) AS c FROM chunks")
        return (r or {}).get("c", 0)


class ConversationRepository:
    def __init__(self, mysql: MySQLStore):
        self._m = mysql

    async def ensure_schema(self):
        """收敛运行时建表: conversation_messages(CREATE含user_id + 老库ALTER补). 替代query._ensure_conv_table."""
        await self._m.execute(
            "CREATE TABLE IF NOT EXISTS conversation_messages ("
            "id BIGINT AUTO_INCREMENT PRIMARY KEY, conversation_id VARCHAR(64) NOT NULL, "
            "user_id VARCHAR(50) DEFAULT 'default', role VARCHAR(16) NOT NULL, content TEXT NOT NULL, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "INDEX idx_conv (conversation_id), INDEX idx_user (user_id)"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4")
        cols = {r["Field"] for r in await self._m.fetch("SHOW COLUMNS FROM conversation_messages")}
        if "user_id" not in cols:
            await self._m.execute("ALTER TABLE conversation_messages ADD COLUMN user_id VARCHAR(50) DEFAULT 'default'")

    async def save_message(self, conversation_id: str, user_id: str, role: str, content: str):
        await self._m.execute(
            "INSERT INTO conversation_messages (conversation_id, user_id, role, content) VALUES (%s,%s,%s,%s)",
            conversation_id, user_id, role, content)

    async def load_history(self, conversation_id: str) -> list[ConversationMessage]:
        rows = await self._m.fetch(
            "SELECT role, content FROM conversation_messages WHERE conversation_id=%s ORDER BY id", conversation_id)
        return [ConversationMessage(conversation_id=conversation_id, role=r["role"], content=r["content"]) for r in rows]

    async def list_conversations(self, user_id: str) -> list[ConversationSummary]:
        rows = await self._m.fetch(
            "SELECT conversation_id, MIN(content) AS first_msg, MAX(created_at) AS last_at, COUNT(*) AS msg_cnt "
            "FROM conversation_messages WHERE user_id=%s GROUP BY conversation_id ORDER BY last_at DESC", user_id)
        return [ConversationSummary(id=r["conversation_id"], title=r["first_msg"] or "",
                                   messages=r["msg_cnt"], updated_at=r["last_at"]) for r in rows]

    async def get_messages(self, conversation_id: str, user_id: str) -> list[ConversationMessage]:
        rows = await self._m.fetch(
            "SELECT role, content, created_at FROM conversation_messages "
            "WHERE conversation_id=%s AND user_id=%s ORDER BY id", conversation_id, user_id)
        return [ConversationMessage(conversation_id=conversation_id, role=r["role"],
                                   content=r["content"], created_at=r["created_at"]) for r in rows]

    async def count_distinct_conversations(self) -> int:
        r = await self._m.fetchrow("SELECT COUNT(DISTINCT conversation_id) AS c FROM conversation_messages")
        return (r or {}).get("c", 0)

    async def count_messages(self) -> int:
        r = await self._m.fetchrow("SELECT COUNT(*) AS c FROM conversation_messages")
        return (r or {}).get("c", 0)
