# ponytail: 依赖装配 — FastAPI lifespan 初始化共享resources(mysql连接池), Depends注入
# 取代全局可变单例(query.py _retriever/_graph) + 模块级new(documents oss_store) + 每端点new+connect+close
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


@dataclass
class AppResources:
    """应用级共享资源(启动时建一次, 所有端点经 Depends 取)."""
    settings: Settings
    mysql: MySQLStore      # 共享连接池(不再每端点 connect/close)
    qdrant: QdrantWriter
    oss: OssStore
    retriever: HybridRetriever
    graph: Any             # compiled langgraph


@asynccontextmanager
async def lifespan(app: FastAPI):
    # ponytail: 启动建共享resources(mysql建池一次); 停止关池
    s = get_settings()
    mysql = MySQLStore(cfg=s.mysql_cfg)
    await mysql.connect()
    qdrant = QdrantWriter(cfg=s.qdrant_cfg)
    oss = OssStore(cfg=s.oss_cfg)
    # ponytail: 阶段5 — retriever注入共享实例(不再retriever内重复new)
    retriever = HybridRetriever(qdrant=qdrant, oss=oss, mysql=mysql, settings=s)
    app.state.resources = AppResources(
        settings=s, mysql=mysql, qdrant=qdrant, oss=oss,
        retriever=retriever, graph=build_graph(retriever),
    )
    yield
    await mysql.close()


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
