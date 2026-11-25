# src/cache: 缓存系统 - Redis查询缓存(精确键+租户generation失效; 语义候选默认关)
# ponytail: DocumentCache已删(评审P2死代码)

from src.cache.query_cache import (
    QueryCache,
    HybridCache,
    get_hybrid_cache
)

__all__ = [
    "QueryCache",
    "HybridCache",
    "get_hybrid_cache"
]
