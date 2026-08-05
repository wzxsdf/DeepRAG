# ponytail: 文档上传/确认/列表/状态/删除. 删除=软删documents+物理清理Qdrant/OSS/子表(跨存储一致性). 经Repository
from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from pydantic import BaseModel
import uuid
import os

from src.storage.oss import OssStore
from src.storage.mysql import MySQLStore
from src.storage.qdrant_store import QdrantWriter
from src.storage.repositories import (KBRepository, DocumentRepository,
    DocumentVersionRepository, IndexingJobRepository, ChunkRepository)
from src.security.auth import get_current_user
from src.core.lifespan import get_mysql, get_oss, get_qdrant

router = APIRouter(prefix="/api/v1", tags=["documents"])


async def purge_document(document_id: str, user_id: str, mysql: MySQLStore, oss: OssStore, qdrant: QdrantWriter):
    """物理清理单个文档全部数据 + 软删 documents 行. 供 DELETE /documents 与 DELETE /kbs 复用(DRY).
    顺序: Qdrant(隔离检索)→OSS(释放空间)→MySQL(子表物理删+主表软删). 幂等(删已删无副作用).
    mysql/oss/qdrant 由调用方经 Depends 注入(共享池)."""
    version_repo = DocumentVersionRepository(mysql)
    doc_repo = DocumentRepository(mysql)
    chunk_repo = ChunkRepository(mysql)
    vids = await version_repo.get_ids(document_id)
    # 1. Qdrant 按version删 — 检索立即不再命中(RAG软删关键)
    for vid in vids:
        await qdrant.delete_by_version(vid)
    # 2. OSS — 原文(oss_key) + parsed/{vid}/ + chunks/{vid}/ + parent_chunks/{pid}
    for key in await version_repo.get_oss_keys(document_id):
        await oss.delete(key)
    for vid in vids:
        await oss.delete_by_prefix(f"parsed/{vid}/")
        await oss.delete_by_prefix(f"chunks/{vid}/")
    for pid in await chunk_repo.get_parent_ids(vids):
        await oss.delete(f"parent_chunks/{pid}.txt")
    # 3. MySQL — chunks/indexing_jobs/document_versions 物理删 + documents 软删(事务级联)
    await doc_repo.purge_with_versions(document_id, vids, user_id)


class PresignRequest(BaseModel):
    filename: str
    file_size: int
    content_type: str = "application/pdf"


class PresignResponse(BaseModel):
    upload_url: str
    oss_key: str
    headers: dict


class ConfirmUploadRequest(BaseModel):
    oss_key: str
    title: str
    kb_id: str = "default"
    file_size: int
    sha256: str


class DocumentResponse(BaseModel):
    document_id: str
    version_id: str
    status: str


@router.post("/uploads/presign")
async def presign_upload(req: PresignRequest, oss: OssStore = Depends(get_oss)) -> PresignResponse:
    """生成OSS预签名上传URL"""
    version_id = str(uuid.uuid4())
    # ponytail: OSS key仅ASCII(version_id+扩展名), 规避中文/全角文件名presigned PUT时URL重编码与签名失配(403)
    ext = os.path.splitext(req.filename)[1] or ".pdf"
    if not ext.isascii():
        ext = ".pdf"
    oss_key = f"raw/{version_id}/{version_id}{ext}"
    presign = await oss.presign_upload(oss_key, expires=3600)
    return PresignResponse(upload_url=presign.url, oss_key=presign.key, headers=presign.headers)


@router.post("/documents/confirm")
async def confirm_upload(req: ConfirmUploadRequest, background_tasks: BackgroundTasks,
                         user=Depends(get_current_user),
                         mysql: MySQLStore = Depends(get_mysql)) -> DocumentResponse:
    """确认上传,触发入库. 校验KB可写(非DELETING/DELETED, 并发保护)."""
    document_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    # ponytail: 并发保护 — KB正在删除/已删除则拒绝上传(409)
    kb = await KBRepository(mysql).get(req.kb_id)
    if not kb:
        raise HTTPException(404, "知识库不存在")
    if kb.status in ("DELETING", "DELETED"):
        raise HTTPException(409, "知识库正在删除或已删除, 无法上传")
    await DocumentRepository(mysql).create(document_id, req.kb_id, req.title)
    await DocumentVersionRepository(mysql).create(version_id, document_id, req.oss_key, req.file_size, req.sha256)
    await IndexingJobRepository(mysql).create(job_id, version_id)
    from apps.worker.ingestion import ingest_document
    # ponytail: 传document_id入库→写Qdrant payload真document_id(修原document_id=version_id的bug)
    background_tasks.add_task(ingest_document, version_id, req.oss_key, req.kb_id, document_id)
    return DocumentResponse(document_id=document_id, version_id=version_id, status="PENDING")


@router.get("/documents")
async def list_documents(kb_id: str | None = None, _=Depends(get_current_user),
                         mysql: MySQLStore = Depends(get_mysql)):
    """文档列表(含入库状态, 过滤已软删). 可按kb_id过滤. join documents+versions+jobs."""
    docs = await DocumentRepository(mysql).list_active(kb_id)
    return [{"id": d.id, "title": d.title, "kb_id": d.kb_id, "status": d.status,
             "stage": d.stage, "created_at": str(d.created_at)} for d in docs]


@router.get("/documents/{document_id}/preview")
async def preview_document(document_id: str, mysql: MySQLStore = Depends(get_mysql),
                           oss: OssStore = Depends(get_oss)):
    """返回原文PDF的presigned GET URL, 供前端在线预览."""
    oss_key = await DocumentVersionRepository(mysql).latest_oss_key(document_id)
    if not oss_key:
        raise HTTPException(404, "文档原文未找到")
    url = await oss.presign_get(oss_key, expires=3600)
    return {"url": url}


@router.get("/documents/{document_id}")
async def get_document(document_id: str, mysql: MySQLStore = Depends(get_mysql)):
    """查询文档状态(过滤已软删)."""
    d = await DocumentRepository(mysql).get_detail(document_id)
    if not d:
        raise HTTPException(404, "文档不存在")
    return {"document_id": d.document_id, "title": d.title, "version_id": d.version_id,
            "status": d.status, "stage": d.stage, "error_msg": d.error_msg}


@router.delete("/documents/{document_id}")
async def delete_document(document_id: str, user=Depends(get_current_user),
                          mysql: MySQLStore = Depends(get_mysql),
                          oss: OssStore = Depends(get_oss),
                          qdrant: QdrantWriter = Depends(get_qdrant)):
    """删除文档: 软删documents(审计) + 物理清理Qdrant/OSS/MySQL子表. 幂等(已删返回DELETED)."""
    doc_repo = DocumentRepository(mysql)
    row = await doc_repo.find_for_delete(document_id)
    if not row:
        raise HTTPException(404, "文档不存在")
    if row.get("deleted_at"):
        return {"document_id": document_id, "status": "DELETED"}  # 幂等
    await purge_document(document_id, user["user_id"], mysql, oss, qdrant)
    return {"document_id": document_id, "status": "DELETED"}
