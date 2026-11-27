# ponytail: /query + /query/stream(SSE Agent节点进度流) + 多轮持久化(带user_id). 经Repository
import json
import uuid
import asyncio
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from src.storage.mysql import MySQLStore
from src.storage.repositories import ConversationRepository, KBRepository
from src.security.auth import get_current_user
from apps.api.access import ensure_kb_readable
from src.core.lifespan import get_graph, get_mysql, get_memory_manager, get_memory_queue
from src.core.logging import log
from src.cache import get_hybrid_cache

router = APIRouter(prefix="/api/v1", tags=["query"])


async def _ensure_kbs_readable(kb_ids: list[str] | None, user_id: str, mysql: MySQLStore):
    """P3: 请求里的kb_ids逐个校验可见性 — 私有库非owner→403, 防枚举他人kb_id越权检索."""
    for kb_id in (kb_ids or []):
        ensure_kb_readable(await KBRepository(mysql).get(kb_id), user_id)


async def _load_history(mysql, conv_id, user_id: str | None = None):
    # ponytail: 取原始消息→重组为[{question,answer}]对(供CRAG多轮理解指代). 建表已由lifespan ensure_schema
    # P3: user_id按归属过滤 — 复用他人conversation_id不再能读其历史(防越权注入prompt)
    msgs = await ConversationRepository(mysql).load_history(conv_id, user_id=user_id)
    history, last_user = [], None
    for m in msgs:
        if m.role == "user":
            last_user = m.content
        elif m.role == "assistant" and last_user:
            history.append({"question": last_user, "answer": m.content})
            last_user = None
    return history


async def _manage_history(memory_manager, history):
    """短期记忆治理(即插即用): 治理权在此收敛, 下游 graph/agent 只消费返回值。

    - memory_manager 启用: 超预算时旧历史压成摘要 + 保留近期原文(P0 主方案)。
    - memory_manager 为 None(未启用): 轻量窗口兜底(最近8轮), 防 graph 爆 token。
    """
    if not history:
        return history
    if memory_manager is None:
        return history[-8:]  # ponytail: 未启用治理 — 保守窗口兜底
    managed = await memory_manager.manage(history)
    return managed.to_history_list()


def _schedule_memory_write(memory_queue, user_id: str, question: str, answer: str, source: str):
    """A1 写闭环: 入持久化队列(替代create_task, 防崩溃丢记忆)。

    memory_queue 为 None(未启用)则跳过。同步 INSERT(快, ms级), 后台 worker 异步消费。
    持久化保证: 进程崩溃重启后, pending 项继续被消费 → writer.write。
    """
    if memory_queue is None or not answer:
        return
    asyncio.create_task(memory_queue.enqueue(user_id, question, answer, source))


async def _persist(mysql, conv_id, user_id, question, ans):
    # ponytail: 共享池不再connect/close; 落用户问+助手答两条
    repo = ConversationRepository(mysql)
    await repo.save_message(conv_id, user_id, "user", question)
    await repo.save_message(conv_id, user_id, "assistant", ans)


class QueryRequest(BaseModel):
    question: str
    tenant_id: str = "default"  # 已废弃(P3): tenant从JWT→users表贯通, 请求值被忽略(防跨租户伪造)
    kb_ids: list[str] | None = None  # 限定检索的知识库(None/空=全部可见库)
    conversation_id: str | None = None
    mode: str = "quick"  # ponytail: quick/kb/agent, 用户显式选(取代LLM意图识别)


class Citation(BaseModel):
    chunk_id: str
    document_id: str
    page: int


class QueryResponse(BaseModel):
    answer: str
    citations: list[Citation]
    retry_count: int
    grounded: bool
    conversation_id: str
    source: str  # 答案来源: kb_grounded/non_kb/open_qa/fallback_unverified/refused


def _step_detail(node: str, update: dict) -> dict:
    """提取节点中间结果(供前端'思考过程'展示). 返回 {title, detail}."""
    if node == "entry":
        m = update.get("mode", "quick")
        it = update.get("intent", "")
        intent_cn = {"chitchat": "闲聊/能力"}.get(it, "")
        detail = f"模式: {m}" + (f"，{intent_cn}直达" if intent_cn else "")
        return {"title": "入口分发", "detail": detail}
    if node == "retrieve":
        n = len(update.get("documents") or [])
        return {"title": "混合检索", "detail": f"检索到 {n} 条证据"}
    if node == "grade":
        n = len(update.get("documents") or [])
        dec = update.get("search_decision", "")
        return {"title": "证据评估", "detail": f"{n} 条相关，进入{'生成' if dec == 'generate' else (dec or '判定')}"}
    if node == "rewrite":
        q = (update.get("query") or "")[:60]
        return {"title": "查询改写", "detail": f"改写为「{q}」（第{update.get('retry_count', 0)}次）"}
    if node == "generate":
        return {"title": "生成回答", "detail": "基于证据合成最终回答"}
    if node == "verify":
        ver = update.get("verification") or {}
        g = "✓" if ver.get("grounded") else "✗"
        c = "✓" if ver.get("citations_valid") else "✗"
        return {"title": "校验引用", "detail": f"证据支撑 {g}，引用有效 {c}"}
    if node == "reply_direct":
        return {"title": "直接回复", "detail": "闲聊/能力问询"}
    if node == "tool_agent":
        return {"title": "调用工具", "detail": "获取实时/外部信息"}
    if node == "plan_agent":
        return {"title": "多源编排", "detail": "规划并组合多源信息"}
    return {"title": node, "detail": ""}


@router.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest, user: dict = Depends(get_current_user),
                graph=Depends(get_graph), mysql: MySQLStore = Depends(get_mysql),
                memory_manager=Depends(get_memory_manager),
                memory_queue=Depends(get_memory_queue)):
    # P3多租户: 强制鉴权(user_id/tenant_id从JWT→users表贯通, 请求体tenant_id被忽略防伪造);
    # kb_ids逐个校验可见性(私有库非owner→403); 缓存键加user_id(不同用户不共享答案缓存)
    conv_id = req.conversation_id or str(uuid.uuid4())
    user_id = user["user_id"]
    tenant_id = user["tenant_id"]
    history = await _manage_history(memory_manager, await _load_history(mysql, conv_id, user_id))
    await _ensure_kbs_readable(req.kb_ids, user_id, mysql)

    # ponytail: 缓存检查 - 先尝试从缓存获取结果(仅非多轮的首问走缓存, 多轮带conversation_id不缓存)
    cache = get_hybrid_cache()
    cache_filters = {
        "tenant_id": tenant_id,
        "user_id": user_id,  # P3: 答案缓存按用户隔离(不同用户同问题不复用)
        "kb_ids": req.kb_ids or [],
        "mode": req.mode
    }
    if not req.conversation_id:  # ponytail: 多轮对话不缓存(上下文相关, 缓存易错)
        cached_result = await cache.get_query_result(req.question, cache_filters, req.mode)
        if cached_result:
            log.info("query_cache_hit", question=req.question[:50] + "...")
            return QueryResponse(
                answer=cached_result.get("answer", ""),
                citations=cached_result.get("citations", []),
                retry_count=cached_result.get("retry_count", 0),
                grounded=cached_result.get("grounded", False),
                conversation_id=conv_id,
                source=cached_result.get("source", "cached")
            )

    # ponytail: P0 checkpoint修复 — thread_id 复用 conversation_id; checkpointer已移除(评审P2),
    # ainvoke直接返回最终state, 多轮上下文每次从MySQL重建
    cfg = {"configurable": {"thread_id": conv_id}}
    values = await graph.ainvoke(
        {"question": req.question, "query": req.question, "tenant_id": tenant_id,
         "retry_count": 0, "documents": [], "search_decision": "", "generation": "", "verification": None,
         "conversation_history": history, "kb_ids": req.kb_ids or [], "intent": "", "source": "",
         "mode": req.mode, "safety": "allow", "user_id": user_id, "memory_context": ""}, config=cfg)
    await _persist(mysql, conv_id, user_id, req.question, values.get("generation", ""))

    docs = values.get("documents", [])
    ver = values.get("verification") or {}
    # A1 写闭环: 入持久化队列(后台worker消费, 防崩溃丢记忆)
    _schedule_memory_write(memory_queue, user_id, req.question,
                           values.get("generation", ""), values.get("source", ""))

    # 构造响应数据
    response_data = {
        "answer": values.get("generation", ""),
        "citations": [{"chunk_id": d.chunk_id, "document_id": d.document_id, "page": d.locator.get("page", 0)} for d in docs],
        "retry_count": values.get("retry_count", 0),
        "grounded": bool(ver.get("grounded")),
        "conversation_id": conv_id,
        "source": values.get("source", "")
    }

    # ponytail: 异步缓存结果 - 不阻塞响应(仅单轮缓存)
    if not req.conversation_id:
        asyncio.create_task(cache.set_query_result(req.question, response_data, cache_filters, req.mode))

    return QueryResponse(**response_data)


@router.post("/query/stream")
async def query_stream(req: QueryRequest, user: dict = Depends(get_current_user),
                       graph=Depends(get_graph), mysql: MySQLStore = Depends(get_mysql),
                       memory_manager=Depends(get_memory_manager),
                       memory_queue=Depends(get_memory_queue)):
    """SSE流式: 推送Agent各节点进度(step事件) + 最终done事件(answer+citations). 非token级(LLM非流), 展示Agent流程.
    P3: 强制鉴权+tenant贯通, 同/query."""
    conv_id = req.conversation_id or str(uuid.uuid4())
    user_id = user["user_id"]
    tenant_id = user["tenant_id"]
    history = await _manage_history(memory_manager, await _load_history(mysql, conv_id, user_id))
    await _ensure_kbs_readable(req.kb_ids, user_id, mysql)
    cfg = {"configurable": {"thread_id": conv_id}}  # ponytail: P0 checkpoint修复 — 复用conversation_id

    async def event_gen():
        # ponytail: 多模式流 — updates(节点中间结果→step事件+最终state累积) + custom(Agent检索推理→thought事件)
        # checkpointer已移除(评审P2): 最终state从updates流累积, 不再aget_state
        state_acc: dict = {}
        async for mode, data in graph.astream(
            {"question": req.question, "query": req.question, "tenant_id": tenant_id,
             "retry_count": 0, "documents": [], "search_decision": "", "generation": "", "verification": None,
             "conversation_history": history, "kb_ids": req.kb_ids or [], "intent": "", "source": "",
             "mode": req.mode, "safety": "allow", "user_id": user_id, "memory_context": ""},
            config=cfg, stream_mode=["updates", "custom"]):
            if mode == "custom":
                # ponytail: tag==token 的custom事件(生成器逐字)走独立token事件, 其余(Agent检索)走thought
                tag = data.get("tag") if isinstance(data, dict) else None
                yield {"event": "token" if tag == "token" else "thought", "data": json.dumps(data, ensure_ascii=False)}
            elif mode == "updates":
                for node, update in data.items():
                    state_acc.update(update)  # 最终state累积(替代aget_state)
                    yield {"event": "step", "data": json.dumps({"node": node, **_step_detail(node, update)}, ensure_ascii=False)}
        values = state_acc
        await _persist(mysql, conv_id, user_id, req.question, values.get("generation", ""))
        docs = values.get("documents", [])
        ver = values.get("verification") or {}
        # A1 写闭环: 入持久化队列(后台worker消费)
        _schedule_memory_write(memory_queue, user_id, req.question,
                               values.get("generation", ""), values.get("source", ""))
        yield {"event": "done", "data": json.dumps({
            "answer": values.get("generation", ""),
            "citations": [{"chunk_id": d.chunk_id, "document_id": d.document_id, "page": d.locator.get("page", 0)} for d in docs],
            "grounded": bool(ver.get("grounded")), "retry_count": values.get("retry_count", 0),
            "conversation_id": conv_id, "source": values.get("source", ""),
        }, ensure_ascii=False)}

    return EventSourceResponse(event_gen())
