# KB 生命周期管理设计（删除 + 改名）

- **日期**：2026-08-02
- **状态**：已设计，待实现
- **决策**：策略 A（元数据软删 + 物理存储立即清理）+ 范围 1（KB 删除/文档删除/KB 改名；文档版本更新下期）

## 1. 背景与现状

| 能力 | 状态 |
|---|---|
| 新增 | ✅ 建 KB、上传文档入库都在 |
| 删除 | ❌ 全缺——无 DELETE 接口；`oss.py` 无删除方法；`qdrant_store.py` 无按条件删除；`mysql.py` autocommit 无事务 |
| 更新 | ❌ 无 PATCH |

三个风险：
1. **无事务**（`mysql.py:30` autocommit=True）——级联删除半途失败留脏数据。
2. **跨存储无分布式事务**——MySQL/Qdrant/OSS 删一半挂了不可回滚。
3. **BUG**（`ingestion_core.py:158`）——Qdrant payload `document_id` 误写为 `version_id`，按 document_id 删 Qdrant 失效。

表结构：`knowledge_bases→documents(kb_id)→document_versions(document_id)→indexing_jobs/chunks(version_id)`，**FK 全无 ON DELETE CASCADE**。

## 2. 设计

### 2.1 删除策略（A）
- **元数据软删**：`knowledge_bases`/`documents` 主表加 `deleted_at`+`deleted_by`，列表/检索过滤。
- **物理存储立即清理**：Qdrant points（隔离检索）、OSS 4 类 key、MySQL 子表（chunks/indexing_jobs/document_versions）物理删。
- **理由**：软删元数据=审计+防误删；Qdrant 立即硬删=RAG 检索隔离（软删的 chunks 不摘除会被检索到=错误答案）。

### 2.2 删文档 `DELETE /documents/{id}`
1. 查 `document_versions`(oss_key) + `chunks`(parent_id 去重)
2. Qdrant `delete_by_version`(version_id) —— 隔离检索
3. OSS 删：原文(oss_key) + `parsed/{vid}/document.json` + `chunks/{vid}/*` + `parent_chunks/{pid}*`
4. MySQL 事务：`DELETE chunks WHERE version_id IN(…)` + `DELETE indexing_jobs` + `DELETE document_versions` + `UPDATE documents SET deleted_at,deleted_by`
5. 幂等：已删（deleted_at IS NOT NULL）→ 200 直接返回

### 2.3 删 KB `DELETE /kbs/{id}`
1. 事务内置 `status=DELETING`（禁并发上传）
2. 查所有 `document_id`(WHERE kb_id AND deleted_at IS NULL) → 逐个走删文档流程
3. Qdrant `delete_by_kb`(kb_id) 兜底一次清
4. `UPDATE knowledge_bases SET deleted_at,deleted_by,status=DELETED`
5. 幂等

### 2.4 一致性
- MySQL 级联用事务（新增 `execute_transaction`）
- Qdrant/OSS 在事务外，幂等可重试
- 顺序：先 Qdrant（隔离检索）→ OSS（释放空间）→ MySQL 事务（元数据）

### 2.5 并发保护
- `knowledge_bases.status`：ACTIVE/DELETING/DELETED
- `documents/confirm` 检查 kb status，DELETING/DELETED 拒绝上传
- 检索：Qdrant 物理删天然隔离；MySQL 列表过滤 `deleted_at IS NULL`

### 2.6 审计
- `deleted_by`（token 取 user_id）+ `deleted_at`；软删记录保留可查。

### 2.7 BUG 修复
- `ingestion_core.py:158` payload `document_id`写真 document_id。

## 3. 表结构变更
- `knowledge_bases`：+`deleted_at TIMESTAMP NULL`、+`deleted_by VARCHAR(50)`、+`status VARCHAR(20) DEFAULT 'ACTIVE'`
- `documents`：+`deleted_at TIMESTAMP NULL`、+`deleted_by VARCHAR(50)`
- 兼容已存在表：`init_mysql.py` 加 `ALTER ADD COLUMN`（列已存在则跳过）

## 4. 接口
- `DELETE /api/v1/kbs/{kb_id}` → 200 / 404 / 409(并发)
- `PATCH /api/v1/kbs/{kb_id}` `{name}` → 200
- `DELETE /api/v1/documents/{document_id}` → 200 / 404
- `GET /kbs`、`GET /documents` 列表过滤 `deleted_at IS NULL`
- `POST /documents/confirm` 检查 kb status

## 5. YAGNI（不做）
异步清理 job（同步够）、恢复接口、独立 audit_logs 表、文档版本更新。
