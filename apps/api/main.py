# ponytail: 最小化API, 挂载 documents + query 路由

import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

from .documents import router as documents_router
from .kb import router as kb_router
from .query import router as query_router
from .auth import router as auth_router
from .monitor import router as monitor_router
from .conversations import router as conversations_router

load_dotenv()

app = FastAPI(title="DeepRAG")
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
async def health():
    # ponytail: 实际ping主要依赖
    status = {"api": "ok", "mysql": "unknown", "qdrant": "unknown"}
    try:
        from src.storage.mysql import MySQLStore
        m = MySQLStore(host=os.getenv("MYSQL_HOST"), port=int(os.getenv("MYSQL_PORT")),
                       db=os.getenv("MYSQL_DATABASE", os.getenv("MYSQL_DB", "deeprag")),
                       user=os.getenv("MYSQL_USER"), password=os.getenv("MYSQL_PASSWORD"))
        await m.connect()
        await m.close()
        status["mysql"] = "ok"
    except Exception as e:
        status["mysql"] = f"error: {type(e).__name__}"
    try:
        from src.storage.qdrant_store import QdrantWriter
        q = QdrantWriter(host=os.getenv("QDRANT_HOST"), port=int(os.getenv("QDRANT_PORT")),
                         api_key=os.getenv("QDRANT_API_KEY", ""))
        q.client.get_collection(q.collection_name)
        status["qdrant"] = "ok"
    except Exception as e:
        status["qdrant"] = f"error: {type(e).__name__}"
    return status


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=os.getenv("API_HOST", "0.0.0.0"), port=int(os.getenv("API_PORT", "8000")))
