# Redis缓存系统 - 查询+文档混合策略

from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
import redis.asyncio as redis
import json
import hashlib
from dataclasses import dataclass, asdict

from src.core.logging import log


@dataclass
class CacheStats:
    """缓存统计信息"""
    hits: int = 0  # 缓存命中次数
    misses: int = 0  # 缓存未命中次数
    evictions: int = 0  # 缓存驱逐次数
    size: int = 0  # 当前缓存大小
    hit_rate: float = 0.0  # 命中率


class QueryCache:
    """查询结果缓存 - 智能缓存策略

    策略：
    - 问题级别缓存：相同问题直接返回结果
    - 语义相似度缓存：语义相似的问题共享缓存
    - 时间窗口：近期查询优先
    - LRU淘汰：最少使用优先淘汰
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/1",
        ttl: int = 3600,  # 1小时过期
        max_size: int = 1000,  # 最大缓存数量
        enable_semantic: bool = True,  # 是否启用语义相似度缓存
        semantic_threshold: float = 0.85  # 语义相似度阈值
    ):
        self.redis_url = redis_url
        self.ttl = ttl
        self.max_size = max_size
        self.enable_semantic = enable_semantic
        self.semantic_threshold = semantic_threshold
        self._redis_client = None
        self._stats = CacheStats()

    async def _get_redis(self) -> redis.Redis:
        """获取Redis客户端"""
        if self._redis_client is None:
            self._redis_client = await redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True
            )
        return self._redis_client

    def _generate_cache_key(
        self,
        question: str,
        filters: Dict[str, Any],
        mode: str = "query"
    ) -> str:
        """生成缓存键

        Args:
            question: 查询问题
            filters: 过滤条件
            mode: 查询模式

        Returns:
            str: 缓存键
        """
        # 标准化输入
        normalized_question = question.strip().lower()

        # 构建缓存内容
        cache_content = {
            "question": normalized_question,
            "filters": sorted(filters.items()),  # 排序确保一致性
            "mode": mode
        }

        # 生成哈希
        content_str = json.dumps(cache_content, sort_keys=True)
        return f"query_cache:{hashlib.md5(content_str.encode()).hexdigest()}"

    async def get(
        self,
        question: str,
        filters: Dict[str, Any] = None,
        mode: str = "query"
    ) -> Optional[Dict[str, Any]]:
        """获取缓存结果

        Args:
            question: 查询问题
            filters: 过滤条件
            mode: 查询模式

        Returns:
            Dict: 缓存结果，未命中返回None
        """
        filters = filters or {}
        cache_key = self._generate_cache_key(question, filters, mode)

        redis_client = await self._get_redis()

        try:
            # 1. 精确匹配
            result = await redis_client.get(cache_key)
            if result:
                self._stats.hits += 1
                self._update_stats()
                log.info("cache_hit", key=cache_key[:16] + "...", type="exact")
                return json.loads(result)

            # 2. 语义相似度匹配（如果启用）
            if self.enable_semantic:
                semantic_result = await self._semantic_cache_lookup(question, filters, mode)
                if semantic_result:
                    self._stats.hits += 1
                    self._update_stats()
                    log.info("cache_hit", type="semantic", similarity=semantic_result.get("similarity"))
                    return semantic_result["result"]

            # 3. 未命中
            self._stats.misses += 1
            self._update_stats()
            log.info("cache_miss", key=cache_key[:16] + "...")
            return None

        except Exception as e:
            log.warning("cache_get_failed", error=str(e))
            self._stats.misses += 1
            return None

    async def set(
        self,
        question: str,
        result: Dict[str, Any],
        filters: Dict[str, Any] = None,
        mode: str = "query",
        ttl: Optional[int] = None
    ) -> bool:
        """设置缓存结果

        Args:
            question: 查询问题
            result: 查询结果
            filters: 过滤条件
            mode: 查询模式
            ttl: 过期时间（可选）

        Returns:
            bool: 是否设置成功
        """
        filters = filters or {}
        cache_key = self._generate_cache_key(question, filters, mode)

        redis_client = await self._get_redis()

        try:
            # 序列化结果
            cache_data = {
                "question": question,
                "result": result,
                "filters": filters,
                "mode": mode,
                "timestamp": datetime.now().isoformat(),
                "ttl": ttl or self.ttl
            }

            # 检查缓存大小，必要时清理
            await self._ensure_cache_size()

            # 存储到Redis
            cache_ttl = ttl or self.ttl
            await redis_client.setex(
                cache_key,
                cache_ttl,
                json.dumps(cache_data)
            )

            # 更新统计
            self._stats.size += 1
            self._update_stats()

            log.info("cache_set", key=cache_key[:16] + "...", ttl=cache_ttl)
            return True

        except Exception as e:
            log.warning("cache_set_failed", error=str(e))
            return False

    async def invalidate(
        self,
        question: str,
        filters: Dict[str, Any] = None,
        mode: str = "query"
    ) -> bool:
        """使缓存失效

        Args:
            question: 查询问题
            filters: 过滤条件
            mode: 查询模式

        Returns:
            bool: 是否删除成功
        """
        filters = filters or {}
        cache_key = self._generate_cache_key(question, filters, mode)

        redis_client = await self._get_redis()

        try:
            # 删除精确匹配缓存
            await redis_client.delete(cache_key)

            # 如果启用语义缓存，也需要处理相关缓存
            if self.enable_semantic:
                await self._invalidate_semantic_cache(question, filters, mode)

            log.info("cache_invalidated", key=cache_key[:16] + "...")
            return True

        except Exception as e:
            log.warning("cache_invalidate_failed", error=str(e))
            return False

    async def _semantic_cache_lookup(
        self,
        question: str,
        filters: Dict[str, Any],
        mode: str
    ) -> Optional[Dict[str, Any]]:
        """语义相似度缓存查找

        Args:
            question: 查询问题
            filters: 过滤条件
            mode: 查询模式

        Returns:
            Dict: 缓存结果（包含相似度分数）
        """
        # 这里简化实现：在实际应用中需要向量相似度计算
        # 使用简单的关键词匹配作为替代
        from src.memory.user_profile import UserPreferenceLearner
        from src.core.config import get_settings

        try:
            # 获取偏好学习器
            # 注意：这里需要传递redis_client，暂时跳过
            # learner = get_preference_learner()

            # 提取查询关键词
            keywords = self._extract_keywords(question)

            # 扫描所有查询缓存键
            redis_client = await self._get_redis()
            pattern = "query_cache:*"

            async for key in redis_client.scan_iter(match=pattern, count=100):
                try:
                    cached = await redis_client.get(key)
                    if not cached:
                        continue

                    cache_data = json.loads(cached)
                    cached_question = cache_data.get("question", "")

                    # 计算关键词重叠度
                    cached_keywords = self._extract_keywords(cached_question)
                    overlap = len(set(keywords) & set(cached_keywords))

                    if overlap >= 2:  # 至少2个关键词匹配
                        similarity = min(overlap / max(len(keywords), len(cached_keywords)), 0.95)

                        if similarity >= self.semantic_threshold:
                            return {
                                "result": cache_data["result"],
                                "similarity": similarity,
                                "cache_key": key
                            }

                except Exception:
                    continue

            return None

        except Exception as e:
            log.warning("semantic_cache_lookup_failed", error=str(e))
            return None

    async def _invalidate_semantic_cache(
        self,
        question: str,
        filters: Dict[str, Any],
        mode: str
    ) -> None:
        """使语义缓存失效"""
        # 简化实现：在当前版本中，语义缓存是临时的
        pass

    def _extract_keywords(self, text: str) -> List[str]:
        """提取关键词（简化版本）

        Args:
            text: 输入文本

        Returns:
            List[str]: 关键词列表
        """
        # 简单实现：分词+去重
        # 在实际应用中可以用更复杂的NLP
        import jieba
        try:
            words = jieba.cut(text)
            stopwords = {"的", "是", "在", "和", "与", "或", "等", "了", "有", "不", "怎么", "什么", "如何", "为什么"}
            keywords = [w for w in words if len(w) > 1 and w not in stopwords]
            return list(set(keywords))[:10]  # 最多10个关键词
        except Exception:
            # 如果jieba不可用，回退到简单分词
            words = text.replace("？", "").replace("？", "").split()
            return [w for w in words if len(w) > 2][:10]

    async def _ensure_cache_size(self) -> None:
        """确保缓存大小在限制内"""
        redis_client = await self._get_redis()

        # 获取当前缓存大小
        pattern = "query_cache:*"
        current_size = 0

        async for key in redis_client.scan_iter(match=pattern, count=100):
            current_size += 1

        # 如果超过最大大小，删除最老的缓存
        if current_size >= self.max_size:
            excess_count = current_size - self.max_size + 1

            # 获取所有缓存键并按时间排序
            cache_keys = []
            async for key in redis_client.scan_iter(match=pattern, count=100):
                try:
                    ttl = await redis_client.ttl(key)
                    cache_keys.append((key, ttl))
                except Exception:
                    continue

            # 按TTL排序，删除即将过期的缓存
            cache_keys.sort(key=lambda x: x[1] if x[1] > 0 else 999999)

            # 删除excess_count个最老的缓存
            for key, _ in cache_keys[:excess_count]:
                await redis_client.delete(key)
                self._stats.evictions += 1

    def _update_stats(self) -> None:
        """更新统计信息"""
        total = self._stats.hits + self._stats.misses
        if total > 0:
            self._stats.hit_rate = self._stats.hits / total

    async def get_stats(self) -> CacheStats:
        """获取缓存统计信息

        Returns:
            CacheStats: 统计信息
        """
        return self._stats

    async def reset_stats(self) -> None:
        """重置统计信息"""
        self._stats = CacheStats()

    async def clear_all(self) -> int:
        """清空所有缓存

        Returns:
            int: 清空的缓存数量
        """
        redis_client = await self._get_redis()
        pattern = "query_cache:*"

        count = 0
        async for key in redis_client.scan_iter(match=pattern, count=1000):
            await redis_client.delete(key)
            count += 1

        await self.reset_stats()
        log.info("cache_cleared", count=count)
        return count


class DocumentCache:
    """文档缓存 - 知识库文档内容缓存

    策略：
    - 文档级别缓存：整个文档的内容缓存
    - 分块缓存：文档chunks的缓存
    - 自动预热：常用文档预加载
    - 智能失效：文档更新时自动失效
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379/2",
        ttl: int = 7200,  # 2小时过期
        max_size: int = 500  # 最大文档缓存数量
    ):
        self.redis_url = redis_url
        self.ttl = ttl
        self.max_size = max_size
        self._redis_client = None

    async def _get_redis(self) -> redis.Redis:
        """获取Redis客户端"""
        if self._redis_client is None:
            self._redis_client = await redis.from_url(
                self.redis_url,
                encoding="utf-8",
                decode_responses=True
            )
        return self._redis_client

    def _generate_cache_key(
        self,
        document_id: str,
        chunk_id: Optional[str] = None,
        tenant_id: Optional[str] = None
    ) -> str:
        """生成文档缓存键

        Args:
            document_id: 文档ID
            chunk_id: 分块ID（可选）
            tenant_id: 租户ID

        Returns:
            str: 缓存键
        """
        if chunk_id:
            return f"doc_cache:{tenant_id or 'default'}:{document_id}:{chunk_id}"
        return f"doc_cache:{tenant_id or 'default'}:{document_id}"

    async def get_document(
        self,
        document_id: str,
        tenant_id: str = "default"
    ) -> Optional[Dict[str, Any]]:
        """获取文档缓存

        Args:
            document_id: 文档ID
            tenant_id: 租户ID

        Returns:
            Dict: 文档缓存数据，未命中返回None
        """
        cache_key = self._generate_cache_key(document_id, tenant_id=tenant_id)

        redis_client = await self._get_redis()

        try:
            cached = await redis_client.get(cache_key)
            if cached:
                log.info("document_cache_hit", doc_id=document_id[:16] + "...")
                return json.loads(cached)
            return None

        except Exception as e:
            log.warning("document_cache_get_failed", doc_id=document_id[:16] + "...", error=str(e))
            return None

    async def set_document(
        self,
        document_id: str,
        document_data: Dict[str, Any],
        tenant_id: str = "default",
        ttl: Optional[int] = None
    ) -> bool:
        """设置文档缓存

        Args:
            document_id: 文档ID
            document_data: 文档数据
            tenant_id: 租户ID
            ttl: 过期时间（可选）

        Returns:
            bool: 是否设置成功
        """
        cache_key = self._generate_cache_key(document_id, tenant_id=tenant_id)

        redis_client = await self._get_redis()

        try:
            cache_data = {
                "document_id": document_id,
                "document_data": document_data,
                "tenant_id": tenant_id,
                "cached_at": datetime.now().isoformat(),
                "ttl": ttl or self.ttl
            }

            # 检查缓存大小
            await self._ensure_cache_size()

            await redis_client.setex(
                cache_key,
                ttl or self.ttl,
                json.dumps(cache_data)
            )

            log.info("document_cache_set", doc_id=document_id[:16] + "...", ttl=ttl or self.ttl)
            return True

        except Exception as e:
            log.warning("document_cache_set_failed", doc_id=document_id[:16] + "...", error=str(e))
            return False

    async def invalidate_document(
        self,
        document_id: str,
        tenant_id: str = "default"
    ) -> bool:
        """使文档缓存失效

        Args:
            document_id: 文档ID
            tenant_id: 租户ID

        Returns:
            bool: 是否删除成功
        """
        # 删除主文档缓存
        doc_key = self._generate_cache_key(document_id, tenant_id=tenant_id)

        # 删除所有分块缓存
        pattern = f"doc_cache:{tenant_id}:{document_id}:*"

        redis_client = await self._get_redis()

        try:
            # 删除主缓存
            await redis_client.delete(doc_key)

            # 删除所有分块缓存
            async for key in redis_client.scan_iter(match=pattern, count=100):
                await redis_client.delete(key)

            log.info("document_cache_invalidated", doc_id=document_id[:16] + "...")
            return True

        except Exception as e:
            log.warning("document_cache_invalidate_failed", doc_id=document_id[:16] + "...", error=str(e))
            return False

    async def _ensure_cache_size(self) -> None:
        """确保缓存大小在限制内"""
        redis_client = await self._get_redis()

        # 获取当前文档缓存大小
        pattern = "doc_cache:*"
        current_size = 0

        async for key in redis_client.scan_iter(match=pattern, count=100):
            current_size += 1

        # 如果超过最大大小，删除最老的缓存
        if current_size >= self.max_size:
            # 这里简化处理：删除部分缓存
            # 在实际应用中可以按访问时间排序
            keys_to_delete = list(redis_client.scan_iter(match=pattern, count=50))

            for key in keys_to_delete[:(current_size - self.max_size)]:
                await redis_client.delete(key)


class HybridCache:
    """混合缓存管理器 - 查询缓存 + 文档缓存"""

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        query_cache_ttl: int = 3600,
        document_cache_ttl: int = 7200
    ):
        self.query_cache = QueryCache(
            redis_url=f"{redis_url}/1",
            ttl=query_cache_ttl
        )
        self.document_cache = DocumentCache(
            redis_url=f"{redis_url}/2",
            ttl=document_cache_ttl
        )

    async def get_query_result(
        self,
        question: str,
        filters: Dict[str, Any] = None,
        mode: str = "query"
    ) -> Optional[Dict[str, Any]]:
        """获取查询结果缓存"""
        return await self.query_cache.get(question, filters or {}, mode)

    async def set_query_result(
        self,
        question: str,
        result: Dict[str, Any],
        filters: Dict[str, Any] = None,
        mode: str = "query"
    ) -> bool:
        """设置查询结果缓存"""
        return await self.query_cache.set(question, result, filters or {}, mode)

    async def invalidate_query(
        self,
        question: str,
        filters: Dict[str, Any] = None,
        mode: str = "query"
    ) -> bool:
        """使查询缓存失效"""
        return await self.query_cache.invalidate(question, filters or {}, mode)

    async def get_document(self, document_id: str, tenant_id: str = "default") -> Optional[Dict[str, Any]]:
        """获取文档缓存"""
        return await self.document_cache.get_document(document_id, tenant_id)

    async def set_document(
        self,
        document_id: str,
        document_data: Dict[str, Any],
        tenant_id: str = "default",
        ttl: Optional[int] = None
    ) -> bool:
        """设置文档缓存"""
        return await self.document_cache.set_document(document_id, document_data, tenant_id, ttl)

    async def invalidate_document(self, document_id: str, tenant_id: str = "default") -> bool:
        """使文档缓存失效"""
        return await self.document_cache.invalidate_document(document_id, tenant_id)

    async def get_stats(self) -> Dict[str, CacheStats]:
        """获取所有缓存统计"""
        query_stats = await self.query_cache.get_stats()
        return {
            "query_cache": query_stats,
            "document_cache": CacheStats(size=0)  # 文档缓存统计可以后续添加
        }

    async def clear_all(self) -> Dict[str, int]:
        """清空所有缓存"""
        query_count = await self.query_cache.clear_all()
        # 文档缓存清理可以后续添加
        return {
            "query_cache": query_count,
            "document_cache": 0
        }


# 全局单例
_hybrid_cache = None


def get_hybrid_cache(
    redis_url: str = None,
    **kwargs
) -> HybridCache:
    """获取混合缓存管理器单例

    Args:
        redis_url: Redis连接URL
        **kwargs: 其他参数

    Returns:
        HybridCache: 混合缓存管理器实例
    """
    global _hybrid_cache

    from src.core.config import get_settings
    settings = get_settings()

    if _hybrid_cache is None:
        _hybrid_cache = HybridCache(
            redis_url=redis_url or settings.redis_url,
            **kwargs
        )

    return _hybrid_cache


__all__ = [
    "QueryCache",
    "DocumentCache",
    "HybridCache",
    "get_hybrid_cache"
]
