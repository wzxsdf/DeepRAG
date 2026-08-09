# ponytail: 最小化API, 挂载路由; lifespan装配共享resources

from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware

from src.core.lifespan import lifespan, get_resources
from src.core.logging import setup_logging
from .documents import router as documents_router
from .kb import router as kb_router
from .query import router as query_router
from .auth import router as auth_router
from .monitor import router as monitor_router
from .conversations import router as conversations_router

setup_logging()  # ponytail: 尽早配置structlog(任何log前)
app = FastAPI(
    title="DeepRAG API",
    description="""
## DeepRAG - Agentic RAG 智能问答系统

基于 CRAG 纠正式检索增强生成的 Agentic RAG 系统，支持多模式问答、知识库管理、流式输出。

### 核心功能

* **智能问答** - 三种模式(快速/知识库/智能体)，支持多轮对话和引用追溯
* **知识库管理** - 文档上传、解析、入库、删除全生命周期
* **流式输出** - SSE实时推送Agent思考过程和答案
* **多租户隔离** - 租户级数据隔离和权限控制

### 查询模式

| 模式 | 说明 | 适用场景 |
|------|------|----------|
| `quick` | 快速问答 | 简单问题、闲聊 |
| `kb` | 知识库问答 | 事实查询、文档检索 |
| `agent` | 智能体 | 复杂任务、多源编排 |

### 答案来源标识

* `kb_grounded` - 知识库(已引用)
* `tool` - 工具调用(实时数据)
* `planned` - 多源编排
* `non_kb` - 非知识库
* `fallback_unverified` - 常识(未验证)
* `refused` - 拒绝回答
""",
    version="1.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    openapi_tags=[
        {"name": "auth", "description": "用户认证(JWT登录/注册)"},
        {"name": "query", "description": "智能问答(单轮/流式/多轮)"},
        {"name": "conversations", "description": "对话历史管理"},
        {"name": "knowledge-base", "description": "知识库管理(增删改查)"},
        {"name": "documents", "description": "文档管理(上传/解析/删除)"},
        {"name": "monitor", "description": "系统监控(健康检查/统计)"},
    ],
    lifespan=lifespan
)
# ponytail: CORS允许前端(Vite默认5173)跨域调用
app.add_middleware(CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000"],
    allow_credentials=True, allow_methods=["*"], allow_headers=["*"])
app.include_router(auth_router)
app.include_router(monitor_router)
app.include_router(conversations_router)
app.include_router(documents_router)
app.include_router(kb_router)
app.include_router(query_router)


@app.get("/")
async def root():
    return {"status": "ok", "message": "DeepRAG API"}


@app.get("/health")
async def health(res=Depends(get_resources)):
    """依赖健康(ping共享resources)."""
    status = {"api": "ok", "mysql": "unknown", "qdrant": "unknown"}
    try:
        async with res.mysql.pool.acquire() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
        status["mysql"] = "ok"
    except Exception as e:
        status["mysql"] = f"error: {type(e).__name__}"
    try:
        res.qdrant.client.get_collection(res.qdrant.collection_name)
        status["qdrant"] = "ok"
    except Exception as e:
        status["qdrant"] = f"error: {type(e).__name__}"
    return status


if __name__ == "__main__":
    import uvicorn
    from src.core.config import get_settings
    s = get_settings()
    uvicorn.run(app, host=s.api_host, port=s.api_port)
