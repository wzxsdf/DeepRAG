# 对话记忆管理 - Redis+MySQL混合存储

from datetime import datetime
from typing import List, Dict, Any
import redis.asyncio as redis
import json
import hashlib

from src.core.logging import log


class ConversationMessage:
    """对话消息模型"""

    def __init__(
        self,
        role: str,  # "user" 或 "assistant"
        content: str,
        timestamp: datetime = None,
        metadata: Dict[str, Any] = None,
        message_id: str = None
    ):
        self.role = role
        self.content = content
        self.timestamp = timestamp or datetime.now()
        self.metadata = metadata or {}
        self.message_id = message_id or self._generate_id()

    def _generate_id(self) -> str:
        """生成消息ID"""
        content = f"{self.role}:{self.timestamp}:{self.content[:50]}"
        return hashlib.md5(content.encode()).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "message_id": self.message_id,
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConversationMessage":
        """从字典创建实例"""
        return cls(
            role=data["role"],
            content=data["content"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            metadata=data.get("metadata", {}),
            message_id=data.get("message_id")
        )


class ConversationMemory:
    """对话记忆管理 - Redis+MySQL混合存储

    策略：
    - Redis：热数据，最近N条对话，毫秒级访问
    - MySQL：冷数据，完整历史，持久化保证
    - 自动同步：Redis写操作异步同步到MySQL
    - 智能失效：LRU淘汰策略
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/0",
        mysql_store=None,
        max_redis_messages: int = 100,  # Redis中最多保存的消息数
        redis_ttl: int = 3600 * 24 * 7,  # Redis数据过期时间(7天)
        auto_sync: bool = True  # 是否自动同步到MySQL
    ):
        self.redis_url = redis_url
        self.mysql_store = mysql_store
        self.max_redis_messages = max_redis_messages
        self.redis_ttl = redis_ttl
        self.auto_sync = auto_sync
        self._redis_client = None

    async def _get_redis(self) -> redis.Redis:
        """获取Redis客户端（懒加载）"""
        if self._redis_client is None:
            self._redis_client = await redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True
            )
        return self._redis_client

    async def store(
        self,
        session_id: str,
        role: str,
        content: str,
        metadata: Dict[str, Any] = None
    ) -> str:
        """存储对话消息

        Args:
            session_id: 会话ID
            role: 角色 ("user" 或 "assistant")
            content: 消息内容
            metadata: 额外元数据

        Returns:
            str: 消息ID
        """
        message = ConversationMessage(
            role=role,
            content=content,
            metadata=metadata or {}
        )

        redis_client = await self._get_redis()

        # 1. 存储到Redis（热数据）
        redis_key = f"conversation:{session_id}"
        message_data = message.to_dict()

        # 使用列表存储最新消息
        await redis_client.lpush(redis_key, json.dumps(message_data))

        # 保持Redis中不过多消息
        await redis_client.ltrim(redis_key, 0, self.max_redis_messages - 1)

        # 设置过期时间
        await redis_client.expire(redis_key, self.redis_ttl)

        # 2. 异步同步到MySQL（如果启用）
        if self.auto_sync and self.mysql_store:
            await self._sync_to_mysql(session_id, message)

        # 3. 更新会话元数据
        await self._update_session_metadata(session_id, role)

        log.info(
            "message_stored",
            session_id=session_id,
            message_id=message.message_id,
            role=role,
            content_length=len(content)
        )

        return message.message_id

    async def recall(
        self,
        session_id: str,
        limit: int = 10,
        offset: int = 0
    ) -> List[ConversationMessage]:
        """检索对话历史

        Args:
            session_id: 会话ID
            limit: 返回消息数量
            offset: 偏移量

        Returns:
            List[ConversationMessage]: 消息列表（按时间顺序）
        """
        redis_client = await self._get_redis()
        redis_key = f"conversation:{session_id}"

        # 1. 先从Redis获取（热数据）
        messages_json = await redis_client.lrange(
            redis_key,
            offset,
            offset + limit - 1
        )

        messages = []
        for msg_json in messages_json:
            try:
                msg_data = json.loads(msg_json)
                messages.append(ConversationMessage.from_dict(msg_data))
            except Exception as e:
                log.warning("failed_to_parse_message", error=str(e))

        # 2. 如果Redis中消息不足，从MySQL补充（冷数据）
        if len(messages) < limit and self.mysql_store:
            missing_count = limit - len(messages)
            mysql_messages = await self._fetch_from_mysql(
                session_id,
                missing_count,
                offset + len(messages)
            )
            messages.extend(mysql_messages)

        log.info(
            "messages_retrieved",
            session_id=session_id,
            count=len(messages),
            source="redis+mysql" if self.mysql_store else "redis"
        )

        return messages

    async def get_session_info(self, session_id: str) -> Dict[str, Any]:
        """获取会话信息

        Args:
            session_id: 会话ID

        Returns:
            Dict: 会话元信息
        """
        redis_client = await self._get_redis()
        metadata_key = f"session_metadata:{session_id}"

        metadata_json = await redis_client.get(metadata_key)
        if metadata_json:
            try:
                return json.loads(metadata_json)
            except Exception:
                pass

        # 如果Redis中没有，返回默认信息
        return {
            "session_id": session_id,
            "message_count": 0,
            "last_activity": None,
            "created_at": None
        }

    async def clear_session(self, session_id: str) -> None:
        """清除会话记忆

        Args:
            session_id: 会话ID
        """
        redis_client = await self._get_redis()

        # 删除Redis中的对话历史
        await redis_client.delete(f"conversation:{session_id}")

        # 删除元数据
        await redis_client.delete(f"session_metadata:{session_id}")

        # 如果有MySQL，也删除那里的数据
        if self.mysql_store:
            await self._clear_from_mysql(session_id)

        log.info("session_cleared", session_id=session_id)

    async def _update_session_metadata(self, session_id: str, role: str) -> None:
        """更新会话元数据

        Args:
            session_id: 会话ID
            role: 最后消息角色
        """
        redis_client = await self._get_redis()
        metadata_key = f"session_metadata:{session_id}"

        # 获取现有元数据
        metadata_json = await redis_client.get(metadata_key)
        if metadata_json:
            try:
                metadata = json.loads(metadata_json)
            except Exception:
                metadata = {}
        else:
            metadata = {
                "session_id": session_id,
                "message_count": 0,
                "last_activity": None,
                "created_at": datetime.now().isoformat()
            }

        # 更新元数据
        metadata["message_count"] = metadata.get("message_count", 0) + 1
        metadata["last_activity"] = datetime.now().isoformat()
        metadata["last_role"] = role

        # 存回Redis
        await redis_client.setex(
            metadata_key,
            self.redis_ttl,
            json.dumps(metadata)
        )

    async def _sync_to_mysql(self, session_id: str, message: ConversationMessage) -> None:
        """异步同步消息到MySQL

        Args:
            session_id: 会话ID
            message: 消息对象
        """
        if not self.mysql_store:
            return

        try:
            # 这里需要实现MySQL存储逻辑
            # 假设有一个conversation_messages表
            await self.mysql_store.execute(
                """INSERT INTO conversation_messages
                   (id, session_id, role, content, timestamp, metadata)
                   VALUES (%s, %s, %s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE
                   content = VALUES(content),
 metadata = VALUES(metadata)""",
                message.message_id,
                session_id,
                message.role,
                message.content,
                message.timestamp,
                json.dumps(message.metadata)
            )
        except Exception as e:
            log.warning("mysql_sync_failed", session_id=session_id, error=str(e))

    async def _fetch_from_mysql(
        self,
        session_id: str,
        limit: int,
        offset: int
    ) -> List[ConversationMessage]:
        """从MySQL获取历史消息

        Args:
            session_id: 会话ID
            limit: 消息数量
            offset: 偏移量

        Returns:
            List[ConversationMessage]: 消息列表
        """
        if not self.mysql_store:
            return []

        try:
            rows = await self.mysql_store.fetch(
                """SELECT id, role, content, timestamp, metadata
                   FROM conversation_messages
                   WHERE session_id = %s
                   ORDER BY timestamp DESC
                   LIMIT %s OFFSET %s""",
                session_id,
                limit,
                offset
            )

            messages = []
            for row in rows:
                try:
                    messages.append(ConversationMessage(
                        role=row["role"],
                        content=row["content"],
                        timestamp=row["timestamp"],
                        metadata=json.loads(row["metadata"]) if row["metadata"] else {},
                        message_id=row["id"]
                    ))
                except Exception as e:
                    log.warning("failed_to_parse_mysql_message", error=str(e))

            # 按时间正序返回
            return list(reversed(messages))

        except Exception as e:
            log.warning("mysql_fetch_failed", session_id=session_id, error=str(e))
            return []

    async def _clear_from_mysql(self, session_id: str) -> None:
        """从MySQL清除会话数据

        Args:
            session_id: 会话ID
        """
        if not self.mysql_store:
            return

        try:
            await self.mysql_store.execute(
                "DELETE FROM conversation_messages WHERE session_id = %s",
                session_id
            )
        except Exception as e:
            log.warning("mysql_clear_failed", session_id=session_id, error=str(e))

    async def get_recent_sessions(
        self,
        limit: int = 20,
        activity_since: datetime = None
    ) -> List[Dict[str, Any]]:
        """获取最近的活跃会话

        Args:
            limit: 返回会话数量
            activity_since: 活动时间起点

        Returns:
            List[Dict]: 会话信息列表
        """
        redis_client = await self._get_redis()

        # 扫描所有会话元数据键
        pattern = "session_metadata:*"
        sessions = []

        async for key in redis_client.scan_iter(match=pattern, count=100):
            try:
                metadata_json = await redis_client.get(key)
                if metadata_json:
                    metadata = json.loads(metadata_json)

                    # 过滤条件
                    if activity_since:
                        last_activity = metadata.get("last_activity")
                        if last_activity:
                            last_activity_dt = datetime.fromisoformat(last_activity)
                            if last_activity_dt < activity_since:
                                continue

                    sessions.append(metadata)

                    if len(sessions) >= limit:
                        break
            except Exception as e:
                log.warning("failed_to_parse_session_metadata", key=key, error=str(e))

        # 按最后活动时间排序
        sessions.sort(
            key=lambda x: x.get("last_activity", ""),
            reverse=True
        )

        return sessions[:limit]

    async def close(self) -> None:
        """关闭Redis连接"""
        if self._redis_client:
            await self._redis_client.close()
            self._redis_client = None


# 全局单例
_conversation_memory = None


def get_conversation_memory(
    redis_url: str = None,
    mysql_store=None,
    **kwargs
) -> ConversationMemory:
    """获取对话记忆管理器单例

    Args:
        redis_url: Redis连接URL
        mysql_store: MySQL存储实例
        **kwargs: 其他参数

    Returns:
        ConversationMemory: 记忆管理器实例
    """
    global _conversation_memory

    from src.core.config import get_settings
    settings = get_settings()

    if _conversation_memory is None:
        _conversation_memory = ConversationMemory(
            redis_url=redis_url or settings.redis_url,
            mysql_store=mysql_store,
            **kwargs
        )

    return _conversation_memory


__all__ = [
    "ConversationMemory",
    "ConversationMessage",
    "get_conversation_memory"
]
