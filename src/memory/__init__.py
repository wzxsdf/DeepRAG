# src/memory: 记忆模块 - 短期记忆治理、对话历史、用户偏好、智能检索
#
# P0 已就绪: ShortTermMemoryManager(滑动窗口+摘要), 已接入 query.py, 即插即用。
# P1+ 未接入: ConversationMemory/UserPreferenceLearner/MemoryRetriever(设计稿, 待后续阶段)。

# === P0: 短期记忆治理(已接入) ===
from src.memory.short_term import (
    ShortTermMemoryManager,
    ManagedHistory,
    MemoryConfig,
    get_short_term_manager,
    set_short_term_manager,
)

# === P1: 长期记忆存储(已接入) ===
from src.memory.long_term import (
    LongTermMemoryStore,
    default_embedder,
    EMBED_DIM,
)

# === P2: 实体记忆 + 写闭环(已接入) ===
from src.memory.user_facts import UserFactStore
from src.memory.writer import MemoryWriter
from src.memory.queue import MemoryWriteQueue

# === P3: 记忆整合(已接入) ===
from src.memory.consolidator import MemoryConsolidator

# === P1+ 设计稿(未接入, 保留) ===
from src.memory.conversation import (
    ConversationMemory,
    ConversationMessage,
    get_conversation_memory,
)

from src.memory.user_profile import (
    UserProfile,
    UserPreferenceLearner,
    get_preference_learner,
)

from src.memory.retriever import (
    MemoryRetriever,
    get_memory_retriever,
)

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
    # P1+ 设计稿
    "ConversationMemory",
    "ConversationMessage",
    "get_conversation_memory",
    "UserProfile",
    "UserPreferenceLearner",
    "get_preference_learner",
    "MemoryRetriever",
    "get_memory_retriever",
]
