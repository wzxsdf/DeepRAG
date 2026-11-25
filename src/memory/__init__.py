# src/memory: 记忆模块 - 短期治理(P0) / 长期记忆(P1) / 实体记忆+写闭环(P2) / 整合(P3)
#
# ponytail: 设计稿三件套(conversation/user_profile/retriever, ~1.4k行从未接线)已删除(评审P2减法);
# 在线记忆链路以 lifespan 注入的组件为准: ShortTermMemoryManager / LongTermMemoryStore /
# UserFactStore / MemoryWriter / MemoryWriteQueue / MemoryConsolidator。

# === P0: 短期记忆治理(已接入 query.py) ===
from src.memory.short_term import (
    ShortTermMemoryManager,
    ManagedHistory,
    MemoryConfig,
    get_short_term_manager,
    set_short_term_manager,
)

# === P1: 长期记忆存储(已接入 lifespan/graph) ===
from src.memory.long_term import (
    LongTermMemoryStore,
    default_embedder,
    EMBED_DIM,
)

# === P2: 实体记忆 + 写闭环(已接入) ===
from src.memory.user_facts import UserFactStore
from src.memory.writer import MemoryWriter
from src.memory.queue import MemoryWriteQueue

# === P3: 记忆整合(已接入 lifespan/API) ===
from src.memory.consolidator import MemoryConsolidator

__all__ = [
    # P0 短期记忆治理
    "ShortTermMemoryManager",
    "ManagedHistory",
    "MemoryConfig",
    "get_short_term_manager",
    "set_short_term_manager",
    # P1 长期记忆存储
    "LongTermMemoryStore",
    "default_embedder",
    "EMBED_DIM",
    # P2 实体记忆 + 写闭环
    "UserFactStore",
    "MemoryWriter",
    "MemoryWriteQueue",
    # P3 记忆整合
    "MemoryConsolidator",
]
