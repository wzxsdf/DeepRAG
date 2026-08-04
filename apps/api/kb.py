# ponytail: 知识库CRUD + 生命周期(创建/列表/改名/删除). 删除=软删元数据+物理清理Qdrant/OSS/子表

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
import uuid
import os
from dotenv import load_dotenv

from src.storage.mysql import MySQLStore
from src.security.auth import get_current_user
# ponytail: 复用documents的purge_document+oss_store+_qdrant(DRY, 删KB=循环删其下文档)
from apps.api.documents import purge_document, oss_store, _qdrant

load_dotenv()
router = APIRouter(prefix="/api/v1", tags=["knowledge-bases"])


def _mysql():
    return MySQLStore(host=os.getenv("MYSQL_HOST", ""), port=int(os.getenv("MYSQL_PORT", "3306")),
                      db=os.getenv("MYSQL_DATABASE", os.getenv("MYSQL_DB", "deeprag")),
                      user=os.getenv("MYSQL_USER", ""), password=os.getenv("MYSQL_PASSWORD", ""))


class KbCreate(BaseModel):
    name: str


class KbRename(BaseModel):
    name: str


@router.post("/kbs")
async def create_kb(req: KbCreate, _=Depends(get_current_user)):
    """创建知识库."""
    kb_id = str(uuid.uuid4())
    mysql = _mysql()
    await mysql.connect()
    try:
        await mysql.execute("INSERT IGNORE INTO tenants (id, name) VALUES ('default', 'Default')")
        await mysql.execute("INSERT INTO knowledge_bases (id, tenant_id, name) VALUES (%s, %s, %s)",
                            kb_id, "default", req.name)
    finally:
        await mysql.close()
    return {"id": kb_id, "name": req.name}


@router.get("/kbs")
async def list_kbs(_=Depends(get_current_user)):
    """知识库列表(含文档数, 过滤已软删的kb与文档)."""
    mysql = _mysql()
    await mysql.connect()
    try:
        rows = await mysql.fetch("""
            SELECT kb.id, kb.name, kb.created_at, COUNT(d.id) AS doc_count
            FROM knowledge_bases kb
            LEFT JOIN documents d ON d.kb_id = kb.id AND d.deleted_at IS NULL
            WHERE kb.deleted_at IS NULL
            GROUP BY kb.id, kb.name, kb.created_at
            ORDER BY kb.created_at DESC
        """)
    finally:
        await mysql.close()
    return [{"id": r["id"], "name": r["name"], "doc_count": r["doc_count"],
             "created_at": str(r["created_at"])} for r in rows]


@router.patch("/kbs/{kb_id}")
async def rename_kb(kb_id: str, req: KbRename, _=Depends(get_current_user)):
    """重命名知识库(元数据更新)."""
    mysql = _mysql()
    await mysql.connect()
    try:
        row = await mysql.get_kb(kb_id)
        if not row:
            raise HTTPException(404, "知识库不存在")
        await mysql.execute("UPDATE knowledge_bases SET name=%s WHERE id=%s", req.name, kb_id)
    finally:
        await mysql.close()
    return {"id": kb_id, "name": req.name}


@router.delete("/kbs/{kb_id}")
async def delete_kb(kb_id: str, user=Depends(get_current_user)):
    """删除知识库: 软删kb(审计) + 物理清理所有文档(Qdrant/OSS/MySQL子表).
    ponytail: 跨存储一致性 — 单连接内: 置DELETING(禁并发上传)→逐文档purge→Qdrant按kb兜底→软删kb. 幂等."""
    if kb_id == "default":
        raise HTTPException(403, "默认知识库不可删除")
    mysql = _mysql()
    await mysql.connect()
    try:
        # ponytail: 幂等检查用不过滤deleted_at的查询(已删KB也认,返回DELETED); get_kb过滤了已删会误404
        row = await mysql.fetchrow("SELECT id, deleted_at FROM knowledge_bases WHERE id=%s", kb_id)
        if not row:
            raise HTTPException(404, "知识库不存在")
        if row.get("deleted_at"):
            return {"id": kb_id, "status": "DELETED"}  # 幂等: 已删直接返回
        # 1. 置DELETING — confirm上传会校验status拒绝(并发保护)
        await mysql.set_kb_status(kb_id, "DELETING")
        # 2. 逐文档物理清理(Qdrant by version + OSS + MySQL子表软删)
        doc_ids = await mysql.list_document_ids_of_kb(kb_id)
        qdrant = _qdrant()
        for doc_id in doc_ids:
            await purge_document(doc_id, user["user_id"], mysql, oss_store, qdrant)
        # 3. Qdrant 按 kb_id 兜底一次清(防逐version遗漏, 含历史document_id=version_id脏数据)
        await qdrant.delete_by_kb(kb_id)
        # 4. 软删 kb(审计) + 置DELETED
        await mysql.soft_delete_kb(kb_id, user["user_id"])
    finally:
        await mysql.close()
    return {"id": kb_id, "status": "DELETED", "documents_removed": len(doc_ids)}
