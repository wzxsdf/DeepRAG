# ponytail: W1最小MySQL连接,只为入库流水线

import aiomysql
from typing import Optional

class MySQLStore:
    def __init__(
        self,
        host: str = "localhost",
        port: int = 3306,
        db: str = "deeprag",  # ponytail: 支持 MYSQL_DATABASE 和 MYSQL_DB
        user: str = "deeprag",
        password: str = "deeprag_dev",
    ):
        self.host = host
        self.port = port
        self.db = db
        self.user = user
        self.password = password
        self.pool: Optional[aiomysql.Pool] = None

    async def connect(self):
        """创建连接池"""
        self.pool = await aiomysql.create_pool(
            host=self.host,
            port=self.port,
            db=self.db,
            user=self.user,
            password=self.password,
            autocommit=True,
        )

    async def close(self):
        """关闭连接池"""
        if self.pool:
            self.pool.close()
            await self.pool.wait_closed()

    async def execute(self, query: str, *args):
        """执行SQL"""
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(query, args)
                return cursor.rowcount

    async def fetch(self, query: str, *args):
        """查询多行"""
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(query, args)
                return await cursor.fetchall()

    async def fetchrow(self, query: str, *args):
        """查询单行"""
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(query, args)
                return await cursor.fetchone()

    # ponytail: W1只实现入库需要的方法
    async def create_document_version(
        self,
        version_id: str,
        document_id: str,
        oss_key: str,
        file_size: int,
        sha256: str,
    ):
        """创建文档版本"""
        await self.execute(
            "INSERT INTO document_versions (id, document_id, oss_key, file_size, sha256, status) "
            "VALUES (%s, %s, %s, %s, %s, 'UPLOADED')",
            version_id, document_id, oss_key, file_size, sha256,
        )

    async def create_indexing_job(
        self,
        job_id: str,
        version_id: str,
    ):
        """创建入库任务"""
        await self.execute(
            "INSERT INTO indexing_jobs (id, version_id, stage, status) "
            "VALUES (%s, %s, 'PARSING', 'PENDING')",
            job_id, version_id,
        )

    async def update_job_status(
        self,
        job_id: str,
        status: str,
        stage: str = None,
        error_msg: str = None,
    ):
        """更新任务状态"""
        if error_msg:
            await self.execute(
                "UPDATE indexing_jobs SET status = %s, stage = %s, error_msg = %s, updated_at = NOW() "
                "WHERE id = %s",
                status, stage, error_msg, job_id,
            )
        else:
            await self.execute(
                "UPDATE indexing_jobs SET status = %s, stage = %s, updated_at = NOW() "
                "WHERE id = %s",
                status, stage, job_id,
            )

    async def insert_chunk(
        self,
        chunk_id: str,
        version_id: str,
        parent_id: str,
        page: int,
        heading_path: str,
        content_hash: str,
        oss_key: str,
    ):
        """插入chunk元数据"""
        await self.execute(
            "INSERT INTO chunks (id, version_id, parent_id, page, heading_path, content_hash, oss_key) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            chunk_id, version_id, parent_id, page, heading_path, content_hash, oss_key,
        )

    async def execute_transaction(self, queries: list[tuple[str, tuple]]):
        """单连接内执行多条SQL并事务提交(级联删除用, 替代autocommit). 任一失败rollback.
        ponytail: pool.acquire共享一个conn, begin/commit/rollback包事务; Qdrant/OSS在调用方事务外删(幂等)."""
        async with self.pool.acquire() as conn:
            await conn.begin()
            try:
                async with conn.cursor() as cursor:
                    for query, args in queries:
                        await cursor.execute(query, args)
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

    # ponytail: KB/文档生命周期 - 级联删除辅助查询 + 事务级联(企业级跨存储一致性的MySQL侧)
    async def get_version_ids(self, document_id: str) -> list[str]:
        rows = await self.fetch("SELECT id FROM document_versions WHERE document_id=%s", document_id)
        return [r["id"] for r in rows]

    async def get_version_oss_keys(self, document_id: str) -> list[str]:
        rows = await self.fetch("SELECT oss_key FROM document_versions WHERE document_id=%s", document_id)
        return [r["oss_key"] for r in rows if r.get("oss_key")]

    async def get_chunk_parent_ids(self, version_ids: list[str]) -> list[str]:
        if not version_ids:
            return []
        placeholders = ",".join(["%s"] * len(version_ids))
        rows = await self.fetch(
            f"SELECT DISTINCT parent_id FROM chunks WHERE version_id IN ({placeholders}) AND parent_id IS NOT NULL",
            *version_ids)
        return [r["parent_id"] for r in rows]

    async def delete_document_data(self, document_id: str, user_id: str = "default"):
        """事务级联: 物理删 chunks/indexing_jobs/document_versions + 软删 documents(deleted_at/by).
        Qdrant/OSS由调用方在事务外删(幂等). 已删(deleted_at非空)幂等无副作用."""
        vids = await self.get_version_ids(document_id)
        soft = ("UPDATE documents SET deleted_at=NOW(), deleted_by=%s WHERE id=%s AND deleted_at IS NULL",
                (user_id, document_id))
        if not vids:
            await self.execute_transaction([soft])
            return
        placeholders = ",".join(["%s"] * len(vids))
        await self.execute_transaction([
            (f"DELETE FROM chunks WHERE version_id IN ({placeholders})", tuple(vids)),
            (f"DELETE FROM indexing_jobs WHERE version_id IN ({placeholders})", tuple(vids)),
            ("DELETE FROM document_versions WHERE document_id=%s", (document_id,)),
            soft,
        ])

    async def list_document_ids_of_kb(self, kb_id: str) -> list[str]:
        rows = await self.fetch("SELECT id FROM documents WHERE kb_id=%s AND deleted_at IS NULL", kb_id)
        return [r["id"] for r in rows]

    async def set_kb_status(self, kb_id: str, status: str):
        await self.execute("UPDATE knowledge_bases SET status=%s WHERE id=%s", status, kb_id)

    async def soft_delete_kb(self, kb_id: str, user_id: str = "default"):
        await self.execute_transaction([
            ("UPDATE knowledge_bases SET deleted_at=NOW(), deleted_by=%s, status='DELETED' "
             "WHERE id=%s AND deleted_at IS NULL", (user_id, kb_id)),
        ])

    async def get_kb(self, kb_id: str) -> dict | None:
        return await self.fetchrow("SELECT * FROM knowledge_bases WHERE id=%s AND deleted_at IS NULL", kb_id)
