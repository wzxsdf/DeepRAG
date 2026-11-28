# ponytail: 依赖装配 — FastAPI lifespan 初始化共享resources(mysql连接池), Depends注入
# 取代全局可变单例(query.py _retriever/_graph) + 模块级new(documents oss_store) + 每端点new+connect+close
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from fastapi import FastAPI, Request, Depends

from src.core.config import Settings, get_settings
from src.storage.mysql import MySQLStore
from src.storage.qdrant_store import QdrantWriter
from src.storage.oss import OssStore
from src.retrieval.hybrid import HybridRetriever
from src.agents.graph import build_graph
from src.core.llm import ModelGateway, get_gateway
from src.storage.repositories import (UserRepository, ConversationRepository, KBRepository,
                                      DocumentRepository)


@dataclass
class AppResources:
    """应用级共享资源(启动时建一次, 所有端点经 Depends 取)."""
    settings: Settings
    mysql: MySQLStore      # 共享连接池(不再每端点 connect/close)
    qdrant: QdrantWriter
    oss: OssStore
    gateway: ModelGateway  # 统一LLM入口(实例=get_gateway()单例, 与agents/llm.py同源; langfuse trace)
    retriever: HybridRetriever
    graph: Any             # compiled langgraph
    memory_manager: Any = None  # 短期记忆治理器(P0); None=未启用(向后兼容)
    long_term_store: Any = None  # 长期记忆存储(P1); None=未启用(读闭环跳过)
    fact_store: Any = None       # 实体记忆存储(P2); None=未启用(用户画像跳过)
    memory_writer: Any = None    # 记忆写入器(P2); None=未启用(写闭环跳过)
    memory_queue: Any = None     # 写闭环持久化队列(A1); None=未启用(入队跳过)
    consolidator: Any = None     # 记忆整合器(P3); None=未启用(整合跳过)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ponytail: 启动建共享resources(mysql建池一次); 停止关池
    s = get_settings()
    mysql = MySQLStore(cfg=s.mysql_cfg)
    await mysql.connect()
    # ponytail: 阶段4 — 启动收敛运行时DDL(users补name/password_hash列 + conversation_messages建表), 替代路由每次_ensure_*
    await UserRepository(mysql).ensure_schema()
    await ConversationRepository(mysql).ensure_schema()
    await KBRepository(mysql).ensure_schema()  # P3: knowledge_bases补owner_id列(幂等)
    await DocumentRepository(mysql).ensure_schema()  # 架构优化③: documents/versions/chunks幂等补索引
    qdrant = QdrantWriter(cfg=s.qdrant_cfg)
    oss = OssStore(cfg=s.oss_cfg)
    gateway = get_gateway()  # ponytail: 阶段2 — 启动触发ModelGateway初始化(缓存预热+langfuse handler+日志), 与agents/llm.py共用单例
    # ponytail: 阶段5 — retriever注入共享实例(不再retriever内重复new)
    retriever = HybridRetriever(qdrant=qdrant, oss=oss, mysql=mysql, settings=s)
    # ponytail: P0 — 短期记忆治理器(即插即用; config.memory_enabled=False则不注入, query.py跳过治理)
    memory_manager = _build_memory_manager(s)
    # ponytail: P1 — 长期记忆存储(即插即用; memory_long_term_enabled=False则不注入, graph读闭环跳过)
    long_term_store = _build_long_term_store(s)
    # ponytail: P2 — 实体记忆存储(即插即用) + 记忆写入器(写闭环)
    fact_store = _build_fact_store(s, mysql)
    memory_writer = _build_memory_writer(s, fact_store, long_term_store)
    if fact_store is not None:
        await fact_store.ensure_schema()  # 建user_facts表
    # ponytail: A1 — 写闭环持久化队列(替代create_task, 防崩溃丢记忆) + worker消费
    memory_queue = _build_memory_queue(s, mysql)
    write_task = None
    if memory_queue is not None:
        await memory_queue.ensure_schema()
        if memory_writer is not None and s.memory_write_interval > 0:
            write_task = asyncio.create_task(_write_consume_loop(memory_queue, memory_writer, s.memory_write_interval))
    # ponytail: P3 — 记忆整合器(即插即用) + 可选后台定时任务
    consolidator = _build_consolidator(s, long_term_store)
    consolidate_task = None
    if consolidator is not None and s.memory_consolidate_interval > 0:
        consolidate_task = asyncio.create_task(_consolidate_loop(consolidator, s.memory_consolidate_interval))
    app.state.resources = AppResources(
        settings=s, mysql=mysql, qdrant=qdrant, oss=oss, gateway=gateway,
        retriever=retriever, graph=build_graph(retriever, memory_store=long_term_store, fact_store=fact_store,
                                               context_max_chars=s.memory_context_max_chars),
        memory_manager=memory_manager, long_term_store=long_term_store,
        fact_store=fact_store, memory_writer=memory_writer, memory_queue=memory_queue, consolidator=consolidator,
    )
    yield
    if consolidate_task is not None:
        consolidate_task.cancel()  # ponytail: 关停后台整合任务
    if write_task is not None:
        write_task.cancel()  # ponytail: 关停写队列消费worker
    await mysql.close()


async def _write_consume_loop(queue, writer, interval: int):
    """A1 写队列消费worker: 每 interval 秒拉 pending → writer.write → 标记done/failed。

    持久化保证: 进程崩溃重启后, 未消费的 pending 继续被处理(队列在MySQL)。
    """
    from src.core.logging import log
    await asyncio.sleep(interval)  # 启动先等一轮
    while True:
        try:
            items = await queue.fetch_pending(limit=10)
            for item in items:
                try:
                    await writer.write(item.user_id, item.question, item.answer, item.source)
                    await queue.mark_done(item.id)
                except Exception as e:
                    # ponytail: writer.write 内部已 try/except, 到这里属非预期; 记录并重试
                    log.warning("memory_consume_item_error", qid=item.id, error=type(e).__name__, msg=str(e))
                    await queue.mark_failed(item.id, str(e))
        except Exception as e:
            log.warning("memory_consume_loop_error", error=type(e).__name__, msg=str(e))
        await asyncio.sleep(interval)


async def _consolidate_loop(consolidator, interval: int):
    """P3 后台整合循环: 每 interval 秒全量整合一次。异常不退出(持续运行)。"""
    from src.core.logging import log
    await asyncio.sleep(interval)  # 启动后先等一轮(避免启动即跑)
    while True:
        try:
            await consolidator.consolidate_all()
        except Exception as e:
            log.warning("memory_consolidate_loop_error", error=type(e).__name__, msg=str(e))
        await asyncio.sleep(interval)


def _build_memory_manager(settings: Settings):
    """按配置构建短期记忆治理器。enabled=False 返回 None(即插即用: 不注入=不治理)。"""
    from src.memory.short_term import ShortTermMemoryManager, set_short_term_manager
    cfg = settings.memory_config
    if not cfg.enabled:
        return None
    manager = ShortTermMemoryManager(config=cfg)
    set_short_term_manager(manager)  # 同步模块单例(非web场景/脚本共用)
    return manager


def _build_long_term_store(settings: Settings):
    """按配置构建长期记忆存储。enabled=False 返回 None(即插即用: 不注入=读闭环跳过)。"""
    if not settings.memory_long_term_enabled:
        return None
    from src.memory.long_term import LongTermMemoryStore
    return LongTermMemoryStore(qdrant_cfg=settings.qdrant_cfg,
                               decay_lambda=settings.memory_decay_lambda,
                               max_per_user=settings.memory_max_per_user)


def _build_fact_store(settings: Settings, mysql: MySQLStore):
    """按配置构建实体记忆存储。P2 实体记忆读侧需长期记忆开关联动。"""
    if not settings.memory_long_term_enabled:
        return None
    from src.memory.user_facts import UserFactStore
    return UserFactStore(mysql)


def _build_memory_writer(settings: Settings, fact_store, long_term_store):
    """按配置构建记忆写入器。memory_auto_write=False 返回 None(即插即用: 不注入=不沉淀)。"""
    if not settings.memory_auto_write:
        return None
    from src.memory.writer import MemoryWriter
    return MemoryWriter(fact_store=fact_store, long_term_store=long_term_store)


def _build_memory_queue(settings: Settings, mysql: MySQLStore):
    """A1: 构建写闭环持久化队列。memory_auto_write=False 返回 None(不入队)。"""
    if not settings.memory_auto_write:
        return None
    from src.memory.queue import MemoryWriteQueue
    return MemoryWriteQueue(mysql)


def _build_consolidator(settings: Settings, long_term_store):
    """按配置构建记忆整合器。long_term_store 为 None(P1未启用)则不整合。"""
    if long_term_store is None:
        return None
    from src.memory.consolidator import MemoryConsolidator
    return MemoryConsolidator(long_term_store=long_term_store,
                              dedup_threshold=settings.memory_dedup_threshold)


def get_resources(request: Request) -> AppResources:
    """FastAPI Depends 入口: 取 app.state.resources."""
    return request.app.state.resources


def get_mysql(res: AppResources = Depends(get_resources)) -> MySQLStore:
    return res.mysql


def get_qdrant(res: AppResources = Depends(get_resources)) -> QdrantWriter:
    return res.qdrant


def get_oss(res: AppResources = Depends(get_resources)) -> OssStore:
    return res.oss


def get_graph(res: AppResources = Depends(get_resources)):
    return res.graph


def get_memory_manager(res: AppResources = Depends(get_resources)):
    """短期记忆治理器(P0). 返回 None=未启用, query.py 据此跳过治理(即插即用)。"""
    return res.memory_manager


def get_memory_writer(res: AppResources = Depends(get_resources)):
    """记忆写入器(P2). 返回 None=未启用, query.py 据此跳过写闭环(即插即用)。"""
    return res.memory_writer


def get_memory_queue(res: AppResources = Depends(get_resources)):
    """写闭环持久化队列(A1). 返回 None=未启用, query.py 据此跳过入队(即插即用)。"""
    return res.memory_queue


def get_long_term_store(res: AppResources = Depends(get_resources)):
    """长期记忆存储(P1). 返回 None=未启用。供 monitor 指标查询。"""
    return res.long_term_store


def get_fact_store(res: AppResources = Depends(get_resources)):
    """实体记忆存储(P2). 返回 None=未启用。供 monitor 指标查询。"""
    return res.fact_store


def get_consolidator(res: AppResources = Depends(get_resources)):
    """记忆整合器(P3). 返回 None=未启用, API 端点据此跳过(即插即用)。"""
    return res.consolidator
