# KB 生命周期管理 实现计划

**Goal:** KB/文档删除（软删元数据+物理清理三存储）+ KB 改名，企业级跨存储一致性。

**Architecture:** 元数据软删（deleted_at/deleted_by/status）+ Qdrant/OSS/子表物理删；MySQL 事务级联；幂等可重试；并发 DELETING 保护。

## Global Constraints
- 注释中文，`ponytail:` 标简化；不主动 git。
- 测试遵循 `scripts/test_*.py` 端到端风格。
- MySQL 级联删除必须用事务（新增 `execute_transaction`）。
- Qdrant/OSS 删除幂等（删已删不报错）。

## 接口契约
- `MySQLStore.execute_transaction(queries: list[tuple[str,tuple]]) -> None`：单连接内 begin/commit/rollback
- `MySQLStore.soft_delete_document(doc_id, user_id)` / `soft_delete_kb(kb_id, user_id)`
- `OssStore.delete(key)`、`OssStore.delete_by_prefix(prefix)`
- `QdrantWriter.delete_by_kb(kb_id)`、`QdrantWriter.delete_by_version(version_id)`
- KB status: ACTIVE/DELETING/DELETED

---

### Task 1: schema 字段 + init 兼容
**Files:** `src/storage/mysql_schema.sql`, `scripts/init_mysql.py`
- [ ] mysql_schema.sql: knowledge_bases 加 deleted_at/deleted_by/status；documents 加 deleted_at/deleted_by
- [ ] init_mysql.py: 加 ALTER（列已存在跳过，参考 conversation_messages 补 user_id 的模式）
- [ ] 运行 `python scripts/init_mysql.py`，验证无报错、字段存在

### Task 2: oss.py 删除方法
**Files:** `src/storage/oss.py`
- [ ] `delete(key)`：`self.bucket.delete_object(key)`
- [ ] `delete_by_prefix(prefix)`：`list_objects(prefix)` 收集 key + `batch_delete_objects`
- [ ] 幂等：删除不存在的 key 不抛（oss2 静默/捕获 NoSuchKey）

### Task 3: qdrant_store.py 按条件删除
**Files:** `src/storage/qdrant_store.py`
- [ ] `delete_by_kb(kb_id)`：`client.delete("chunks", points_selector=Filter(must=[FieldCondition(key="kb_id", match=MatchValue(kb_id))]))`
- [ ] `delete_by_version(version_id)`：同上 match version_id
- [ ] 幂等：空集不报错

### Task 4: mysql.py 事务 + 级联
**Files:** `src/storage/mysql.py`
- [ ] `execute_transaction(queries)`：单连接 `begin/commit/rollback`（从 pool.acquire 一个 conn）
- [ ] `delete_document_data(doc_id, user_id)`：事务内删 chunks/jobs/versions + UPDATE documents 软删（先查 version_ids）
- [ ] `soft_delete_kb(kb_id, user_id)`：事务内置 status=DELETING→DELETED + deleted_at
- [ ] `list_document_ids_of_kb(kb_id)`、`get_version_oss_keys(doc_id)`、`get_chunk_parent_ids(version_id)` 辅助查询

### Task 5: 修 ingestion_core document_id BUG
**Files:** `apps/worker/ingestion_core.py:158`
- [ ] payload `document_id` 写真 document_id（需把 doc_id 传入 index_chunks_to_qdrant）
- [ ] 沿调用链确认 document_id 可达（ingest_document 已有 doc_id）

### Task 6: kb.py DELETE + PATCH
**Files:** `apps/api/kb.py`
- [ ] `DELETE /kbs/{kb_id}`：status=DELETING→逐文档删(Qdrant/OSS/MySQL)→Qdrant delete_by_kb 兜底→软删 kb
- [ ] `PATCH /kbs/{kb_id}`：改 name（过滤已删）
- [ ] `GET /kbs` 列表过滤 `deleted_at IS NULL`
- [ ] 幂等 + 鉴权(user_id from token)

### Task 7: documents.py DELETE + confirm 校验
**Files:** `apps/api/documents.py`
- [ ] `DELETE /documents/{doc_id}`：Qdrant by version → OSS → MySQL 事务软删
- [ ] `GET /documents`、`GET /documents/{id}` 过滤 deleted_at
- [ ] `POST /documents/confirm`：检查 kb status != DELETING/DELETED，否则 409

### Task 8: 前端 KnowledgeBase.tsx 删除/改名
**Files:** `web/src/pages/KnowledgeBase.tsx`, `web/src/api/kb.ts`
- [ ] KB 卡片：删除按钮 + 确认弹窗（输入名二次确认）+ 改名（inline edit）
- [ ] kb.ts: `deleteKb(id)`, `renameKb(id, name)`
- [ ] 删除后乐观刷新列表 + toast

### Task 9: 端到端 test_kb_lifecycle.py
**Files:** `scripts/test_kb_lifecycle.py`
- [ ] 建 KB→传 2 文档入库→删 1 文档(验证 Qdrant/OSS/子表清, kb 还在)→删 KB(验证全清, status=DELETED)
- [ ] 断言：删后直查 Qdrant 该 kb 0 points；删已删幂等 200；检索不再命中

## Self-Review
- spec §2 删除流程→Task 6/7；事务→Task 4；并发→Task 7 confirm 校验；审计→Task 1 字段+Task 6 user_id；BUG→Task 5。✓
- 无 placeholder；类型一致（execute_transaction/delet_by_* 签名全程统一）。✓
