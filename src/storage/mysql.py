# ponytail: MySQL执行层 — 连接池 + 通用execute/fetch/fetchrow/事务; 领域SQL下沉到Repository(阶段4)

import aiomysql
from typing import Optional

from src.core.config import MySQLCfg


class MySQLStore:
    def __init__(
        self,
        cfg: MySQLCfg | None = None,
        host: str = "localhost",
        port: int = 3306,
        db: str = "deeprag",  # ponytail: 支持 MYSQL_DATABASE 和 MYSQL_DB
        user: str = "deeprag",
        password: str = "deeprag_dev",
    ):
        if cfg is not None:  # ponytail: cfg注入(阶段1), 旧kwargs调用向后兼容
            host, port, db, user, password = cfg.host, cfg.port, cfg.db, cfg.user, cfg.password
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
        """执行SQL(写/DDL), 返回rowcount. autocommit, 每次execute独立连接."""
        async with self.pool.acquire() as conn:
            async with conn.cursor() as cursor:
                await cursor.execute(query, args)
                return cursor.rowcount

    async def fetch(self, query: str, *args):
        """查询多行(dict)"""
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(query, args)
                return await cursor.fetchall()

    async def fetchrow(self, query: str, *args):
        """查询单行(dict|None)"""
        async with self.pool.acquire() as conn:
            async with conn.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(query, args)
                return await cursor.fetchone()

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
