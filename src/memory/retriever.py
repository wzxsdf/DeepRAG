# 记忆检索器 - 语义相似度 + 时间权重

import json
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional
import numpy as np
from sentence_transformers import SentenceTransformer
import redis.asyncio as redis

from src.memory.conversation import ConversationMessage
from src.core.logging import log


class MemoryRetriever:
    """智能记忆检索器 - 语义相似度 + 时间权重混合策略

    策略：
    - 语义相似度：基于Sentence-BERT计算语义相关性
    - 时间衰减：近期记忆权重更高
    - 重要性加权：用户反馈影响记忆权重
    - 多因子融合：综合多个维度计算相关性
    """

    def __init__(
        self,
        embedding_model: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        redis_client: redis.Redis = None,
        semantic_weight: float = 0.7,  # 语义相似度权重
        time_weight: float = 0.2,  # 时间权重
        importance_weight: float = 0.1,  # 重要性权重
        time_decay_factor: float = 0.1,  # 时间衰减因子
        cache_embeddings: bool = True  # 是否缓存嵌入向量
    ):
        self.semantic_weight = semantic_weight
        self.time_weight = time_weight
        self.importance_weight = importance_weight
        self.time_decay_factor = time_decay_factor
        self.cache_embeddings = cache_embeddings

        # 加载嵌入模型
        self.model = SentenceTransformer(embedding_model)
        self.embedding_cache: Dict[str, np.ndarray] = {}

        self.redis_client = redis_client

    async def retrieve_relevant_memories(
        self,
        query: str,
        session_id: str,
        top_k: int = 5,
        time_window: Optional[timedelta] = None
    ) -> List[Dict[str, Any]]:
        """检索相关记忆

        Args:
            query: 查询内容
            session_id: 会话ID
            top_k: 返回结果数量
            time_window: 时间窗口（只检索该时间段内的记忆）

        Returns:
            List[Dict]: 相关记忆列表，按相关性排序
        """
        # 1. 获取会话历史
        from src.memory.conversation import get_conversation_memory
        memory_manager = get_conversation_memory()

        # 获取更多历史用于检索
        messages = await memory_manager.recall(
            session_id,
            limit=top_k * 3  # 获取更多候选
        )

        if not messages:
            return []

        # 2. 过滤时间窗口
        if time_window:
            cutoff_time = datetime.now() - time_window
            messages = [
                m for m in messages
                if m.timestamp >= cutoff_time
            ]

        # 3. 计算每个记忆的相关性分数
        scored_memories = []
        query_embedding = await self._get_embedding(query)

        for message in messages:
            # 3.1 语义相似度分数
            semantic_score = await self._calculate_semantic_similarity(
                query,
                message.content,
                query_embedding
            )

            # 3.2 时间权重分数
            time_score = self._calculate_time_weight(message.timestamp)

            # 3.3 重要性权重分数
            importance_score = self._calculate_importance_weight(message)

            # 3.4 综合分数
            combined_score = (
                self.semantic_weight * semantic_score +
                self.time_weight * time_score +
                self.importance_weight * importance_score
            )

            scored_memories.append({
                "message": message,
                "semantic_score": semantic_score,
                "time_score": time_score,
                "importance_score": importance_score,
                "combined_score": combined_score,
                "relevance_reason": self._explain_relevance(
                    semantic_score, time_score, importance_score
                )
            })

        # 4. 排序并返回top_k
        scored_memories.sort(key=lambda x: x["combined_score"], reverse=True)
        top_memories = scored_memories[:top_k]

        log.info(
            "memories_retrieved",
            session_id=session_id,
            query_length=len(query),
            retrieved_count=len(top_memories),
            avg_score=sum(m["combined_score"] for m in top_memories) / len(top_memories) if top_memories else 0
        )

        return top_memories

    async def _get_embedding(self, text: str) -> np.ndarray:
        """获取文本嵌入向量

        Args:
            text: 输入文本

        Returns:
            np.ndarray: 嵌入向量
        """
        # 检查缓存
        if self.cache_embeddings and text in self.embedding_cache:
            return self.embedding_cache[text]

        # 计算嵌入
        embedding = self.model.encode(text, convert_to_numpy=True)

        # 缓存结果
        if self.cache_embeddings:
            self.embedding_cache[text] = embedding

            # 限制缓存大小
            if len(self.embedding_cache) > 1000:
                # 删除最旧的50个缓存
                oldest_keys = list(self.embedding_cache.keys())[:50]
                for key in oldest_keys:
                    del self.embedding_cache[key]

        return embedding

    async def _calculate_semantic_similarity(
        self,
        query: str,
        message_content: str,
        query_embedding: Optional[np.ndarray] = None
    ) -> float:
        """计算语义相似度

        Args:
            query: 查询文本
            message_content: 记忆内容
            query_embedding: 查询嵌入（可选）

        Returns:
            float: 语义相似度分数 [0, 1]
        """
        if query_embedding is None:
            query_embedding = await self._get_embedding(query)

        message_embedding = await self._get_embedding(message_content)

        # 计算余弦相似度
        similarity = np.dot(query_embedding, message_embedding) / (
            np.linalg.norm(query_embedding) * np.linalg.norm(message_embedding)
        )

        return float(similarity)

    def _calculate_time_weight(self, message_time: datetime) -> float:
        """计算时间权重分数

        策略：近期记忆权重更高，使用指数衰减

        Args:
            message_time: 消息时间

        Returns:
            float: 时间权重分数 [0, 1]
        """
        now = datetime.now()
        time_diff = (now - message_time).total_seconds() / 3600  # 小时差

        # 指数衰减：weight = e^(-λt)
        weight = np.exp(-self.time_decay_factor * time_diff)

        return float(weight)

    def _calculate_importance_weight(self, message: ConversationMessage) -> float:
        """计算重要性权重

        基于消息元数据判断重要性

        Args:
            message: 消息对象

        Returns:
            float: 重要性权重 [0, 1]
        """
        importance = 0.5  # 基础重要性

        metadata = message.metadata

        # 1. 根据角色判断
        if message.role == "user":
            importance += 0.1  # 用户问题更重要

        # 2. 根据内容长度判断
        content_length = len(message.content)
        if content_length > 200:
            importance += 0.1  # 长内容可能更重要
        elif content_length < 50:
            importance -= 0.05  # 短内容重要性较低

        # 3. 根据元数据判断
        if metadata.get("is_key_turn"):
            importance += 0.2

        if metadata.get("user_rating", 0) >= 4:
            importance += 0.15

        if metadata.get("error_occurred"):
            importance -= 0.1

        # 4. 根据对话位置判断
        if metadata.get("conversation_position") == "conclusion":
            importance += 0.1

        return min(max(importance, 0.0), 1.0)  # 限制在[0, 1]范围

    def _explain_relevance(
        self,
        semantic_score: float,
        time_score: float,
        importance_score: float
    ) -> str:
        """解释相关性原因

        Args:
            semantic_score: 语义分数
            time_score: 时间分数
            importance_score: 重要性分数

        Returns:
            str: 相关性解释
        """
        reasons = []

        if semantic_score > 0.7:
            reasons.append("语义高度相关")
        elif semantic_score > 0.5:
            reasons.append("语义相关")

        if time_score > 0.7:
            reasons.append("近期记忆")
        elif time_score > 0.3:
            reasons.append("近期对话")

        if importance_score > 0.7:
            reasons.append("重要对话")

        return "、".join(reasons) if reasons else "一般相关"

    async def find_similar_queries(
        self,
        query: str,
        threshold: float = 0.6,
        limit: int = 5
    ) -> List[Dict[str, Any]]:
        """查找相似的历史查询

        Args:
            query: 当前查询
            threshold: 相似度阈值
            limit: 返回数量

        Returns:
            List[Dict]: 相似查询列表
        """
        # 这里可以从所有会话中查找相似查询
        # 简化实现：从Redis中扫描
        if not self.redis_client:
            return []

        similar_queries = []
        query_embedding = await self._get_embedding(query)

        # 扫描所有会话
        pattern = "conversation:*"
        async for key in self.redis_client.scan_iter(match=pattern, count=50):
            try:
                messages_json = await self.redis_client.lrange(key, 0, 19)  # 获取最近20条

                for msg_json in messages_json:
                    try:
                        msg_data = json.loads(msg_json)

                        # 只看用户消息
                        if msg_data.get("role") == "user":
                            content = msg_data.get("content", "")
                            similarity = await self._calculate_semantic_similarity(
                                query, content, query_embedding
                            )

                            if similarity >= threshold:
                                similar_queries.append({
                                    "query": content,
                                    "similarity": similarity,
                                    "timestamp": msg_data.get("timestamp"),
                                    "session_id": key.split(":")[-1]
                                })

                    except Exception:
                        continue

                if len(similar_queries) >= limit:
                    break

            except Exception:
                continue

        # 按相似度排序
        similar_queries.sort(key=lambda x: x["similarity"], reverse=True)

        return similar_queries[:limit]

    async def get_context_summary(
        self,
        session_id: str,
        max_length: int = 500
    ) -> str:
        """获取会话上下文摘要

        Args:
            session_id: 会话ID
            max_length: 最大长度

        Returns:
            str: 上下文摘要
        """
        from src.memory.conversation import get_conversation_memory
        memory_manager = get_conversation_memory()

        messages = await memory_manager.recall(session_id, limit=10)

        if not messages:
            return "（无对话历史）"

        summary_parts = []
        current_length = 0

        for message in reversed(messages[-5:]):  # 只看最近5条
            part = f"{message.role}: {message.content[:100]}..."
            if current_length + len(part) > max_length:
                break
            summary_parts.append(part)
            current_length += len(part)

        return "\n".join(summary_parts)


# 全局单例
_memory_retriever = None


def get_memory_retriever(
    embedding_model: str = None,
    redis_client: redis.Redis = None,
    **kwargs
) -> MemoryRetriever:
    """获取记忆检索器单例

    Args:
        embedding_model: 嵌入模型名称
        redis_client: Redis客户端
        **kwargs: 其他参数

    Returns:
        MemoryRetriever: 记忆检索器实例
    """
    global _memory_retriever

    if _memory_retriever is None:
        # 默认使用轻量级多语言模型
        model = embedding_model or "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        _memory_retriever = MemoryRetriever(
            embedding_model=model,
            redis_client=redis_client,
            **kwargs
        )

    return _memory_retriever


__all__ = [
    "MemoryRetriever",
    "get_memory_retriever"
]
