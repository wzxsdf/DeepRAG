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
app = FastAPI(title="DeepRAG", lifespan=lifespan)
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
