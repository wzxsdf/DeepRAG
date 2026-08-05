# ponytail: /query + /query/stream(SSE Agent节点进度流) + 多轮持久化(带user_id)
# 阶段3: graph/mysql经Depends注入共享resources(lifespan建), 不再全局懒单例
import json
import uuid
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from src.storage.mysql import MySQLStore
from src.security.auth import oauth2_scheme, decode_token
from src.core.lifespan import get_graph, get_mysql

router = APIRouter(prefix="/api/v1", tags=["query"])


async def _ensure_conv_table(mysql):
    await mysql.execute("""CREATE TABLE IF NOT EXISTS conversation_messages (
        id BIGINT AUTO_INCREMENT PRIMARY KEY,
        conversation_id VARCHAR(64) NOT NULL,
        user_id VARCHAR(50) DEFAULT 'default',
        role VARCHAR(16) NOT NULL,
        content TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        INDEX idx_conv (conversation_id),
        INDEX idx_user (user_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4""")
    cols = {c["Field"] for c in await mysql.fetch("SHOW COLUMNS FROM conversation_messages")}
    if "user_id" not in cols:  # ponytail: 老表补user_id列
        await mysql.execute("ALTER TABLE conversation_messages ADD COLUMN user_id VARCHAR(50) DEFAULT 'default'")


async def _load_history(mysql, conv_id):
    rows = await mysql.fetch("SELECT role, content FROM conversation_messages WHERE conversation_id=%s ORDER BY id", conv_id)
    history, last_user = [], None
    for r in rows:
        if r["role"] == "user":
            last_user = r["content"]
        elif r["role"] == "assistant" and last_user:
            history.append({"question": last_user, "answer": r["content"]})
            last_user = None
    return history


async def _save_message(mysql, conv_id, role, content, user_id="default"):
    await mysql.execute(
        "INSERT INTO conversation_messages (conversation_id, user_id, role, content) VALUES (%s, %s, %s, %s)",
        conv_id, user_id, role, content)


def _user_id_from_token(token):
    if not token:
        return "default"
    return decode_token(token) or "default"


class QueryRequest(BaseModel):
    question: str
    tenant_id: str = "default"
    kb_ids: list[str] | None = None  # 限定检索的知识库(None/空=全部)
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


async def _persist(mysql, conv_id, user_id, question, ans):
    # ponytail: 共享池不再connect/close
    await _save_message(mysql, conv_id, "user", question, user_id)
    await _save_message(mysql, conv_id, "assistant", ans, user_id)


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
async def query(req: QueryRequest, token: str | None = Depends(oauth2_scheme),
                graph=Depends(get_graph), mysql: MySQLStore = Depends(get_mysql)):
    conv_id = req.conversation_id or str(uuid.uuid4())
    user_id = _user_id_from_token(token)
    await _ensure_conv_table(mysql)
    history = await _load_history(mysql, conv_id)

    cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}
    await graph.ainvoke(
        {"question": req.question, "query": req.question, "tenant_id": req.tenant_id,
         "retry_count": 0, "documents": [], "search_decision": "", "generation": "", "verification": None,
         "conversation_history": history, "kb_ids": req.kb_ids or [], "intent": "", "source": "",
         "mode": req.mode, "safety": "allow"}, config=cfg)
    values = (await graph.aget_state(config=cfg)).values
    await _persist(mysql, conv_id, user_id, req.question, values.get("generation", ""))

    docs = values.get("documents", [])
    ver = values.get("verification") or {}
    return QueryResponse(
        answer=values.get("generation", ""),
        citations=[Citation(chunk_id=d.chunk_id, document_id=d.document_id, page=d.locator.get("page", 0)) for d in docs],
        retry_count=values.get("retry_count", 0),
        grounded=bool(ver.get("grounded")),
        conversation_id=conv_id,
        source=values.get("source", ""),
    )


@router.post("/query/stream")
async def query_stream(req: QueryRequest, token: str | None = Depends(oauth2_scheme),
                       graph=Depends(get_graph), mysql: MySQLStore = Depends(get_mysql)):
    """SSE流式: 推送Agent各节点进度(step事件) + 最终done事件(answer+citations). 非token级(LLM非流), 展示Agent流程."""
    conv_id = req.conversation_id or str(uuid.uuid4())
    user_id = _user_id_from_token(token)
    await _ensure_conv_table(mysql)
    history = await _load_history(mysql, conv_id)
    cfg = {"configurable": {"thread_id": str(uuid.uuid4())}}

    async def event_gen():
        # ponytail: 多模式流 — updates(节点中间结果→step事件) + custom(Agent检索推理→thought事件)
        async for mode, data in graph.astream(
            {"question": req.question, "query": req.question, "tenant_id": req.tenant_id,
             "retry_count": 0, "documents": [], "search_decision": "", "generation": "", "verification": None,
             "conversation_history": history, "kb_ids": req.kb_ids or [], "intent": "", "source": "",
             "mode": req.mode, "safety": "allow"},
            config=cfg, stream_mode=["updates", "custom"]):
            if mode == "custom":
                # ponytail: tag==token 的custom事件(生成器逐字)走独立token事件, 其余(Agent检索)走thought
                tag = data.get("tag") if isinstance(data, dict) else None
                yield {"event": "token" if tag == "token" else "thought", "data": json.dumps(data, ensure_ascii=False)}
            elif mode == "updates":
                for node, update in data.items():
                    yield {"event": "step", "data": json.dumps({"node": node, **_step_detail(node, update)}, ensure_ascii=False)}
        values = (await graph.aget_state(config=cfg)).values
        await _persist(mysql, conv_id, user_id, req.question, values.get("generation", ""))
        docs = values.get("documents", [])
        ver = values.get("verification") or {}
        yield {"event": "done", "data": json.dumps({
            "answer": values.get("generation", ""),
            "citations": [{"chunk_id": d.chunk_id, "document_id": d.document_id, "page": d.locator.get("page", 0)} for d in docs],
            "grounded": bool(ver.get("grounded")), "retry_count": values.get("retry_count", 0),
            "conversation_id": conv_id, "source": values.get("source", ""),
        }, ensure_ascii=False)}

    return EventSourceResponse(event_gen())
