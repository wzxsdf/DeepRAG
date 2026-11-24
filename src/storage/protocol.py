# ponytail: 只定义W1需要的方法,不预埋V2

from typing import Protocol, runtime_checkable
from dataclasses import dataclass
from typing import Any

@dataclass
class RetrievalFilter:
    """检索过滤条件"""
    tenant_id: str
    kb_ids: list[str] | None = None  # ponytail: 多KB检索(None=不限)
    acl_tags: list[str] | None = None
    active_version_id: str | None = None
    active_version_ids: list[str] | None = None

@dataclass
class RetrievedDocument:
    """检索结果"""
    chunk_id: str
    document_id: str
    version_id: str
    parent_id: str | None
    content: str
    locator: dict[str, Any]  # {page, heading_path, line, table}
    score: float
    retrieval_source: str  # "dense" / "sparse" / "hybrid_rrf" / "rerank"
    parent_content: str = ""  # W3.2: 父块上下文(parent expansion)

@runtime_checkable
class Retriever(Protocol):
    """检索器接口"""
    async def retrieve(
        self,
        query: str,
        filters: RetrievalFilter,
        top_k: int,
    ) -> list[RetrievedDocument]:
        """召回相关文档"""
        ...

@dataclass
class ObjectMeta:
    """对象元数据"""
    key: str
    size: int
    etag: str | None = None

@dataclass
class PresignedUpload:
    """预签名上传信息"""
    url: str
    key: str
    headers: dict[str, str]

@runtime_checkable
class ObjectStore(Protocol):
    """对象存储接口"""
    async def presign_upload(
        self,
        key: str,
        expires: int = 3600,
    ) -> PresignedUpload:
        """生成预签名上传URL"""
        ...

    async def put(
        self,
        key: str,
        data: bytes,
    ) -> ObjectMeta:
        """上传对象"""
        ...

    async def get(self, key: str) -> bytes:
        """获取对象"""
        ...
