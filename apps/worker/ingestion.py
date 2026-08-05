# ponytail: 异步入库(BackgroundTasks, 非Celery) - 复用ingestion_core(PyMuPDF+SiliconFlow+Qdrant BM25)
# 注: 原Celery方案因redis-py 8.0 RESP3 + 远程Redis(云代理)HELLO不兼容放弃, 改API进程内异步

import os
from dotenv import load_dotenv

load_dotenv()


async def _update_status(version_id, version_status, job_status=None, error=None):
    """更新 document_versions + indexing_jobs 状态(经Repository)."""
    from src.storage.mysql import MySQLStore
    from src.storage.repositories import DocumentVersionRepository, IndexingJobRepository
    mysql = MySQLStore(host=os.getenv("MYSQL_HOST"), port=int(os.getenv("MYSQL_PORT")),
                       db=os.getenv("MYSQL_DATABASE", os.getenv("MYSQL_DB", "deeprag")),
                       user=os.getenv("MYSQL_USER"), password=os.getenv("MYSQL_PASSWORD"))
    await mysql.connect()
    try:
        await DocumentVersionRepository(mysql).update_status(version_id, version_status)
        if job_status:
            await IndexingJobRepository(mysql).update_by_version(version_id, job_status, error)
    finally:
        await mysql.close()


async def ingest_document(version_id: str, oss_key: str, kb_id: str = "default", document_id: str = ""):
    """完整入库: 解析(PyMuPDF)→切分→向量化(SiliconFlow)→写Qdrant+MySQL+OSS. 更新job状态. 供BackgroundTasks."""
    from apps.worker.ingestion_core import parse_document_from_oss, index_chunks_to_qdrant
    try:
        await _update_status(version_id, "PARSING", "RUNNING")
        parsed = await parse_document_from_oss(version_id, oss_key)
        await _update_status(version_id, "INDEXING", "RUNNING")
        await index_chunks_to_qdrant(version_id, parsed["parsed_key"], kb_id=kb_id, document_id=document_id)
        await _update_status(version_id, "INDEXED", "SUCCESS")
    except Exception as e:
        await _update_status(version_id, "FAILED", "FAILED", str(e)[:500])
