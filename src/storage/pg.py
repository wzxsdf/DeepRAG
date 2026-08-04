# ponytail: W1最小PG连接,只为入库流水线

import asyncpg
from typing import Optional

class PostgresStore:
    def __init__(
        self,
        host: str = "localhost",
        port: int = 5432,
        db: str = "deeprag",
        user: str = "deeprag",
        password: str = "deeprag_dev",
    ):
        self.host = host
        self.port = port
        self.db = db
        self.user = user
        self.password = password
        self.pool: Optional[asyncpg.Pool] = None

    async def connect(self):
        """创建连接池"""
        self.pool = await asyncpg.create_pool(
            host=self.host,
            port=self.port,
            database=self.db,
            user=self.user,
            password=self.password,
        )

    async def close(self):
        """关闭连接池"""
        if self.pool:
            await self.pool.close()

    async def execute(self, query: str, *args):
        """执行SQL"""
        async with self.pool.acquire() as conn:
            return await conn.execute(query, *args)

    async def fetch(self, query: str, *args):
        """查询多行"""
        async with self.pool.acquire() as conn:
            return await conn.fetch(query, *args)

    async def fetchrow(self, query: str, *args):
        """查询单行"""
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

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
            "VALUES ($1, $2, $3, $4, $5, 'UPLOADED')",
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
            "VALUES ($1, $2, 'PARSING', 'PENDING')",
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
                "UPDATE indexing_jobs SET status = $1, stage = $2, error_msg = $3, updated_at = NOW() "
                "WHERE id = $4",
                status, stage, error_msg, job_id,
            )
        else:
            await self.execute(
                "UPDATE indexing_jobs SET status = $1, stage = $2, updated_at = NOW() "
                "WHERE id = $3",
                status, stage, job_id,
            )

    async def insert_chunk(
        self,
        chunk_id: str,
        version_id: str,
        parent_id: str,
        page: int,
        heading_path: list,
        content_hash: str,
        oss_key: str,
    ):
        """插入chunk元数据"""
        await self.execute(
            "INSERT INTO chunks (id, version_id, parent_id, page, heading_path, content_hash, oss_key) "
            "VALUES ($1, $2, $3, $4, $5, $6, $7)",
            chunk_id, version_id, parent_id, page, heading_path, content_hash, oss_key,
        )
