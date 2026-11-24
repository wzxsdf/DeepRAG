# ponytail: 知识库CRUD + 生命周期(创建/列表/改名/删除). 删除=软删元数据+物理清理Qdrant/OSS/子表. 经Repository
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
import uuid

from src.storage.mysql import MySQLStore
from src.storage.qdrant_store import QdrantWriter
from src.storage.oss import OssStore
from src.storage.repositories import KBRepository
from src.security.auth import get_current_user
from src.core.lifespan import get_mysql, get_qdrant, get_oss
from apps.api.documents import purge_document  # ponytail: 复用documents的purge(DRY, 删KB=循环删其下文档)

router = APIRouter(prefix="/api/v1", tags=["knowledge-bases"])


class KbCreate(BaseModel):
    name: str


class KbRename(BaseModel):
    name: str


@router.post("/kbs")
async def create_kb(req: KbCreate, _=Depends(get_current_user), mysql: MySQLStore = Depends(get_mysql)):
    """创建知识库."""
    kb_id = str(uuid.uuid4())
    await KBRepository(mysql).create(kb_id, "default", req.name)
    return {"id": kb_id, "name": req.name}


@router.get("/kbs")
async def list_kbs(_=Depends(get_current_user), mysql: MySQLStore = Depends(get_mysql)):
    """知识库列表(含文档数, 过滤已软删的kb与文档)."""
    kbs = await KBRepository(mysql).list_with_doc_count()
    return [{"id": k.id, "name": k.name, "doc_count": k.doc_count, "created_at": str(k.created_at)} for k in kbs]


@router.patch("/kbs/{kb_id}")
async def rename_kb(kb_id: str, req: KbRename, _=Depends(get_current_user), mysql: MySQLStore = Depends(get_mysql)):
    """重命名知识库(元数据更新)."""
    repo = KBRepository(mysql)
    if not await repo.get(kb_id):
        raise HTTPException(404, "知识库不存在")
    await repo.rename(kb_id, req.name)
    return {"id": kb_id, "name": req.name}


@router.delete("/kbs/{kb_id}")
async def delete_kb(kb_id: str, user=Depends(get_current_user),
                    mysql: MySQLStore = Depends(get_mysql),
                    oss: OssStore = Depends(get_oss),
                    qdrant: QdrantWriter = Depends(get_qdrant)):
    """删除知识库: 软删kb(审计) + 物理清理所有文档(Qdrant/OSS/MySQL子表).
    ponytail: 跨存储一致性 — 置DELETING(禁并发上传)→逐文档purge→Qdrant按kb兜底→软删kb. 幂等."""
    if kb_id == "default":
        raise HTTPException(403, "默认知识库不可删除")
    repo = KBRepository(mysql)
    # ponytail: 幂等检查用不过滤deleted_at(find_including_deleted); get过滤了已删会误404
    row = await repo.find_including_deleted(kb_id)
    if not row:
        raise HTTPException(404, "知识库不存在")
    if row.get("deleted_at"):
        return {"id": kb_id, "status": "DELETED"}  # 幂等: 已删直接返回
    await repo.set_status(kb_id, "DELETING")  # 置DELETING — confirm上传会校验status拒绝(并发保护)
    doc_ids = await repo.list_document_ids(kb_id)
    for doc_id in doc_ids:
        await purge_document(doc_id, user["user_id"], mysql, oss, qdrant,
                             user.get("tenant_id", "default"))
    await qdrant.delete_by_kb(kb_id)  # 按 kb_id 兜底一次清(防逐version遗漏, 含历史脏数据)
    await repo.soft_delete(kb_id, user["user_id"])
    return {"id": kb_id, "status": "DELETED", "documents_removed": len(doc_ids)}
