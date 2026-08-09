# 记忆写入队列 — A1 持久化(防 create_task 进程崩溃丢记忆)
#
# 职责: 把"待沉淀的记忆"持久化到 MySQL 队列表, 后台 worker 消费 → writer.write。
# 解耦: 纯组件, 依赖 MySQLStore; query.py 入队(同步INSERT,快), worker 异步消费。
# 可靠: 进程崩溃重启后, pending 项继续被消费; 重试超限标记 failed 不死循环。
#
# 取代 query.py 的 asyncio.create_task(writer.write) —— 那个进程崩溃即丢。

from dataclasses import dataclass
from typing import Optional

from src.core.logging import log


@dataclass
class QueueItem:
    """队列项(消费时还原传给 writer.write)。"""
    id: int
    user_id: str
    question: str
    answer: str
    source: str
    retries: int


class MemoryWriteQueue:
    """记忆写入持久化队列 — 即插即用组件。

    用法:
        q = MemoryWriteQueue(mysql); await q.ensure_schema()
        await q.enqueue("u1", "问", "答", "kb_grounded")  # query.py 同步入队
        items = await q.fetch_pending(10)                   # worker 消费
        await q.mark_done(item.id)                          # 成功
    """

    MAX_RETRIES = 3  # ponytail: 超过此次数标记 failed, 不死循环

    def __init__(self, mysql_store):
        self._m = mysql_store

    async def ensure_schema(self):
        """幂等建队列表。lifespan 启动调用。"""
        await self._m.execute(
            "CREATE TABLE IF NOT EXISTS memory_write_queue ("
            "id BIGINT AUTO_INCREMENT PRIMARY KEY, "
            "user_id VARCHAR(50) NOT NULL, "
            "question TEXT NOT NULL, "
            "answer TEXT NOT NULL, "
            "source VARCHAR(50) DEFAULT '', "
            "status VARCHAR(16) DEFAULT 'pending', "
            "retries INT DEFAULT 0, "
            "error VARCHAR(500) DEFAULT NULL, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "processed_at TIMESTAMP NULL, "
            "INDEX idx_status (status, retries)"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        )

    async def enqueue(self, user_id: str, question: str, answer: str, source: str = "") -> Optional[int]:
        """入队(同步 INSERT, 快)。返回队列 id; 空答案不入队。"""
        if not user_id or not question or not answer:
            return None
        try:
            # ponytail: INSERT 后取自增id(MySQLStore.execute 不返回id, 用 LASTMAX 查)
            await self._m.execute(
                "INSERT INTO memory_write_queue (user_id, question, answer, source) VALUES (%s,%s,%s,%s)",
                user_id, question[:2000], answer[:4000], source,
            )
            row = await self._m.fetchrow("SELECT LAST_INSERT_ID() AS id")
            qid = row["id"] if row else None
            return qid
        except Exception as e:
            log.warning("memory_queue_enqueue_failed", user_id=user_id, error=str(e))
            return None

    async def fetch_pending(self, limit: int = 10) -> list[QueueItem]:
        """取待消费项(retries 未超限的 pending)。"""
        try:
            rows = await self._m.fetch(
                "SELECT id, user_id, question, answer, source, retries FROM memory_write_queue "
                "WHERE status='pending' AND retries < %s ORDER BY id LIMIT %s",
                self.MAX_RETRIES, limit,
            )
            return [QueueItem(id=r["id"], user_id=r["user_id"], question=r["question"],
                              answer=r["answer"], source=r["source"], retries=r["retries"])
                    for r in rows]
        except Exception as e:
            log.warning("memory_queue_fetch_failed", error=str(e))
            return []

    async def mark_done(self, queue_id: int) -> None:
        """标记成功消费。"""
        try:
            await self._m.execute(
                "UPDATE memory_write_queue SET status='done', processed_at=NOW() WHERE id=%s", queue_id
            )
        except Exception as e:
            log.warning("memory_queue_mark_done_failed", qid=queue_id, error=str(e))

    async def mark_failed(self, queue_id: int, error: str) -> None:
        """标记失败(retries+1, 达上限则 status=failed 不再重试)。"""
        try:
            await self._m.execute(
                "UPDATE memory_write_queue SET retries=retries+1, error=%s, "
                "status=CASE WHEN retries+1 >= %s THEN 'failed' ELSE status END WHERE id=%s",
                error[:500], self.MAX_RETRIES, queue_id,
            )
        except Exception as e:
            log.warning("memory_queue_mark_failed_failed", qid=queue_id, error=str(e))

    async def count_pending(self) -> int:
        """待消费数量(F1 指标用)。"""
        try:
            r = await self._m.fetchrow("SELECT COUNT(*) AS c FROM memory_write_queue WHERE status='pending'")
            return r["c"] if r else 0
        except Exception:
            return 0


__all__ = ["MemoryWriteQueue", "QueueItem"]
