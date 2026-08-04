# ponytail: 文档上传/确认/列表/状态/删除. 删除=软删documents+物理清理Qdrant/OSS/子表(跨存储一致性)

from fastapi import APIRouter, HTTPException, BackgroundTasks, Depends
from pydantic import BaseModel
import uuid
import os
from dotenv import load_dotenv

load_dotenv()

from src.storage.oss import OssStore
from src.storage.mysql import MySQLStore
from src.storage.qdrant_store import QdrantWriter
from src.security.auth import get_current_user

router = APIRouter(prefix="/api/v1", tags=["documents"])

oss_store = OssStore(
    endpoint=os.getenv("OSS_ENDPOINT", "https://oss-cn-hangzhou.aliyuncs.com"),
    bucket=os.getenv("OSS_BUCKET", "deeprag-dev"),
    access_key_id=os.getenv("OSS_ACCESS_KEY_ID", ""),
    access_key_secret=os.getenv("OSS_ACCESS_KEY_SECRET", ""),
)


def _mysql():
    # ponytail: 局部MySQLStore(不依赖on_event, 可靠)
    return MySQLStore(
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        db=os.getenv("MYSQL_DATABASE", os.getenv("MYSQL_DB", "deeprag")),
        user=os.getenv("MYSQL_USER", "deeprag"),
        password=os.getenv("MYSQL_PASSWORD", "deeprag_dev"),
    )


def _qdrant():
    return QdrantWriter(host=os.getenv("QDRANT_HOST", "http://localhost"),
                        port=int(os.getenv("QDRANT_PORT", "6333")), api_key=os.getenv("QDRANT_API_KEY", ""))


async def purge_document(document_id: str, user_id: str, mysql: MySQLStore, oss: OssStore, qdrant: QdrantWriter):
    """物理清理单个文档全部数据 + 软删 documents 行. 供 DELETE /documents 与 DELETE /kbs 复用(DRY).
    顺序: Qdrant(隔离检索)→OSS(释放空间)→MySQL事务(子表物理删+主表软删). 幂等(删已删无副作用).
    mysql须已connect(由调用方管生命周期)."""
    vids = await mysql.get_version_ids(document_id)
    # 1. Qdrant 按version删 — 检索立即不再命中(RAG软删关键: 不摘除会被检索到=错误答案)
    for vid in vids:
        await qdrant.delete_by_version(vid)
    # 2. OSS — 原文(oss_key) + parsed/{vid}/ + chunks/{vid}/ + parent_chunks/{pid}
    for key in await mysql.get_version_oss_keys(document_id):
        await oss.delete(key)
    for vid in vids:
        await oss.delete_by_prefix(f"parsed/{vid}/")
        await oss.delete_by_prefix(f"chunks/{vid}/")
    for pid in await mysql.get_chunk_parent_ids(vids):
        await oss.delete(f"parent_chunks/{pid}.txt")
    # 3. MySQL事务 — chunks/indexing_jobs/document_versions 物理删 + documents 软删(审计)
    await mysql.delete_document_data(document_id, user_id)


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
async def presign_upload(req: PresignRequest) -> PresignResponse:
    """生成OSS预签名上传URL"""
    version_id = str(uuid.uuid4())
    # ponytail: OSS key仅ASCII(version_id+扩展名), 规避中文/全角文件名presigned PUT时URL重编码与签名失配(403)
    ext = os.path.splitext(req.filename)[1] or ".pdf"
    if not ext.isascii():
        ext = ".pdf"
    oss_key = f"raw/{version_id}/{version_id}{ext}"
    presign = await oss_store.presign_upload(oss_key, expires=3600)
    return PresignResponse(upload_url=presign.url, oss_key=presign.key, headers=presign.headers)


@router.post("/documents/confirm")
async def confirm_upload(req: ConfirmUploadRequest, background_tasks: BackgroundTasks,
                         user=Depends(get_current_user)) -> DocumentResponse:
    """确认上传,触发入库. 校验KB可写(非DELETING/DELETED, 并发保护)."""
    document_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    job_id = str(uuid.uuid4())
    mysql = _mysql()
    await mysql.connect()
    try:
        # ponytail: 并发保护 — KB正在删除/已删除则拒绝上传(409)
        kb = await mysql.get_kb(req.kb_id)
        if not kb:
            raise HTTPException(404, "知识库不存在")
        if kb.get("status") in ("DELETING", "DELETED"):
            raise HTTPException(409, "知识库正在删除或已删除, 无法上传")
        await mysql.execute("INSERT IGNORE INTO knowledge_bases (id, tenant_id, name) VALUES ('default', 'default', 'Default KB')")
        await mysql.execute("INSERT INTO documents (id, kb_id, title) VALUES (%s, %s, %s)", document_id, req.kb_id, req.title)
        await mysql.create_document_version(version_id=version_id, document_id=document_id,
            oss_key=req.oss_key, file_size=req.file_size, sha256=req.sha256)
        await mysql.create_indexing_job(job_id, version_id)
    finally:
        await mysql.close()
    from apps.worker.ingestion import ingest_document
    # ponytail: 传document_id入库→写Qdrant payload真document_id(修原document_id=version_id的bug)
    background_tasks.add_task(ingest_document, version_id, req.oss_key, req.kb_id, document_id)
    return DocumentResponse(document_id=document_id, version_id=version_id, status="PENDING")


@router.get("/documents")
async def list_documents(kb_id: str | None = None, _=Depends(get_current_user)):
    """文档列表(含入库状态, 过滤已软删). 可按kb_id过滤. join documents+versions+jobs."""
    mysql = _mysql()
    await mysql.connect()
    try:
        sql = """SELECT d.id, d.title, d.kb_id, dv.status AS version_status, j.stage, j.status AS job_status, d.created_at
                 FROM documents d
                 LEFT JOIN document_versions dv ON dv.document_id = d.id
                 LEFT JOIN indexing_jobs j ON j.version_id = dv.id
                 WHERE d.deleted_at IS NULL"""
        if kb_id:
            rows = await mysql.fetch(sql + " AND d.kb_id = %s ORDER BY d.created_at DESC LIMIT 100", kb_id)
        else:
            rows = await mysql.fetch(sql + " ORDER BY d.created_at DESC LIMIT 100")
    finally:
        await mysql.close()
    return [{"id": r["id"], "title": r["title"], "kb_id": r["kb_id"],
             "status": r["version_status"] or r["job_status"] or "UNKNOWN",
             "stage": r["stage"], "created_at": str(r["created_at"])} for r in rows]


@router.get("/documents/{document_id}/preview")
async def preview_document(document_id: str):
    """返回原文PDF的presigned GET URL, 供前端在线预览."""
    mysql = _mysql()
    await mysql.connect()
    try:
        row = await mysql.fetchrow(
            "SELECT oss_key FROM document_versions WHERE document_id=%s ORDER BY created_at DESC LIMIT 1",
            document_id)
    finally:
        await mysql.close()
    if not row or not row.get("oss_key"):
        raise HTTPException(404, "文档原文未找到")
    url = await oss_store.presign_get(row["oss_key"], expires=3600)
    return {"url": url}


@router.get("/documents/{document_id}")
async def get_document(document_id: str):
    """查询文档状态(过滤已软删)."""
    mysql = _mysql()
    await mysql.connect()
    try:
        row = await mysql.fetchrow("""
            SELECT d.id, d.title, dv.status AS version_status, dv.id AS version_id, j.stage, j.status AS job_status, j.error_msg
            FROM documents d
            LEFT JOIN document_versions dv ON dv.document_id = d.id
            LEFT JOIN indexing_jobs j ON j.version_id = dv.id
            WHERE d.id = %s AND d.deleted_at IS NULL
        """, document_id)
    finally:
        await mysql.close()
    if not row:
        raise HTTPException(404, "文档不存在")
    return {"document_id": row["id"], "title": row["title"], "version_id": row["version_id"],
            "status": row["version_status"] or row["job_status"] or "UNKNOWN",
            "stage": row["stage"], "error_msg": row["error_msg"]}


@router.delete("/documents/{document_id}")
async def delete_document(document_id: str, user=Depends(get_current_user)):
    """删除文档: 软删documents(审计) + 物理清理Qdrant/OSS/MySQL子表. 幂等(已删返回DELETED)."""
    mysql = _mysql()
    await mysql.connect()
    try:
        row = await mysql.fetchrow("SELECT deleted_at FROM documents WHERE id=%s", document_id)
        if not row:
            raise HTTPException(404, "文档不存在")
        if row.get("deleted_at"):
            return {"document_id": document_id, "status": "DELETED"}  # 幂等
        await purge_document(document_id, user["user_id"], mysql, oss_store, _qdrant())
    finally:
        await mysql.close()
    return {"document_id": document_id, "status": "DELETED"}
