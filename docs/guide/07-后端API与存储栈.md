# 后端 API 与存储栈

> 后端全景：所有 API 路由 + 三存储分工 + 表结构 + 鉴权 + 监控。涉及 `apps/api/*`、`src/storage/*`、`src/security/auth.py`。

## 一、应用入口

**`apps/api/main.py`**：`FastAPI(title="DeepRAG")`，`include_router` 挂载 6 个子路由。
- **CORS**：允许前端 5173 / 3000（`main.py:19-21`）
- 内置端点：`GET /`（欢迎）、`GET /health`（实际 ping MySQL + Qdrant）
- 启动：`uvicorn.run`，host/port 走环境变量

## 二、API 路由清单

### auth（`/api/v1/auth`）

| 方法 | 路径 | 功能 | 鉴权 |
|---|---|---|---|
| POST | `/register` | 注册（密码≥6位，bcrypt），返 token+user | 公开 |
| POST | `/login` | 邮箱密码登录，返 token+user | 公开 |
| GET | `/me` | 校验 token，返当前用户 | 需登录 |

### knowledge-bases（`/api/v1`）

| 方法 | 路径 | 功能 |
|---|---|---|
| POST | `/kbs` | 创建知识库 |
| GET | `/kbs` | 列表（含 doc_count，过滤已软删） |
| PATCH | `/kbs/{kb_id}` | 改名 |
| DELETE | `/kbs/{kb_id}` | 删除（软删+物理清理三存储，禁删 default） |

### documents + uploads（`/api/v1`）

| 方法 | 路径 | 功能 |
|---|---|---|
| POST | `/uploads/presign` | 生成 OSS 预签名 PUT URL |
| POST | `/documents/confirm` | 确认上传 → 建 doc/version/job → 后台 ingest |
| GET | `/documents` | 文档列表（可按 kb_id 过滤） |
| GET | `/documents/{id}/preview` | 返回原 PDF 预签名 GET URL |
| GET | `/documents/{id}` | 查文档状态 |
| DELETE | `/documents/{id}` | 删文档（软删+物理清理，幂等） |

### query（`/api/v1`）

| 方法 | 路径 | 功能 |
|---|---|---|
| POST | `/query` | 同步问答，返 answer/citations/source |
| POST | `/query/stream` | **SSE 流式**：step 节点进度 + thought/token + done |

### conversations（`/api/v1`）

| 方法 | 路径 | 功能 |
|---|---|---|
| GET | `/conversations` | 当前用户会话列表（按最近活动倒序） |
| GET | `/conversations/{conv_id}/messages` | 会话消息列表（校验用户归属） |

### monitor（`/api/v1`）

| 方法 | 路径 | 功能 |
|---|---|---|
| GET | `/metrics` | 入库统计 + 文档/chunk/会话/消息计数 + 三依赖健康 |

## 三、三存储分工

| 存储 | 角色 | 存什么 | 关键特性 |
|---|---|---|---|
| **MySQL** | 元数据真相源 | KB/文档/版本/chunk 元数据、jobs、会话消息、软删审计 | 事务（`execute_transaction`）、软删字段 |
| **Qdrant** | 检索引擎 | collection `chunks`：dense(1024) + sparse(BM25) + payload(content/kb_id/...) | 服务端 RRF 融合、按 payload Filter 删除 |
| **OSS** | 文件存储 | 原始 PDF / parsed JSON / chunk 文本 / parent 块 | 预签名上传、按 prefix 批量删 |

**关联键**：`version_id` 贯穿三存储（MySQL `document_versions.id`、Qdrant payload、OSS key 前缀），删除时跨存储精确定位。

## 四、表结构概要

| 表 | 关键字段 | 生命周期字段 |
|---|---|---|
| `tenants` | id, name | — |
| `users` | id, tenant_id(FK), email(UNIQUE), name, password_hash | — |
| `knowledge_bases` | id, tenant_id, name, embedding_model, config_json | **status**(ACTIVE/DELETING/DELETED), **deleted_at**, **deleted_by** |
| `documents` | id, kb_id(FK), title, active_version_id | **deleted_at**, **deleted_by** |
| `document_versions` | id, document_id(FK), oss_key, sha256, file_size, status | (物理删) |
| `chunks` | id, version_id(FK), parent_id, page, heading_path, content_hash, oss_key | (物理删) |
| `indexing_jobs` | id, version_id(FK), stage, status, retry_count, error_msg | (物理删) |
| `conversation_messages` | conversation_id, user_id, role, content, created_at | — |

**入库状态机**：`document_versions.status` UPLOADED→PARSING→INDEXING→INDEXED/FAILED；`indexing_jobs.status` PENDING→RUNNING→SUCCESS/FAILED。

> ⚠️ **schema vs 运行时偏差**（文档须留意）：
> - `users` 表的 `password_hash` / `name` 列由运行时 `auth.py:23-27` ALTER 补（schema.sql 未声明）
> - 会话表：schema.sql 写 `conversations` + `messages` 两表，**运行时实际用单表 `conversation_messages`**（`query.py:41-54` 建表 + 补 user_id 列）

## 五、JWT 鉴权

**`src/security/auth.py`**

- **密码**：passlib bcrypt（`hash_password` / `verify_password`，空 hash 返 False）
- **签发**：`create_access_token`，HS256，payload `{sub: user_id, exp}`，**有效期 24h**
- **校验**：`decode_token`，JWTError 返 None
- **FastAPI 依赖**：`oauth2_scheme = OAuth2PasswordBearer(auto_error=False)`——**auto_error=False** 让 query 接口可不带 token（降级 default 用户，匿名友好）
- **`get_current_user`**：解 token → 查 users 表 → 返 `{user_id, tenant_id, email, name}`；无效 → 401

> 💡 query 模块特殊：`_user_id_from_token` 允许无 token 降级为 `"default"`，问答接口对匿名友好。

## 六、监控 Metrics

**`GET /api/v1/metrics`**（需登录）返回：

| 字段 | 内容 |
|---|---|
| `jobs` | indexing_jobs 按 status 分组计数 |
| `documents` | documents 表总行数 |
| `chunks` | chunks 表总行数 |
| `conversations` | conversation_messages 的 COUNT(DISTINCT conversation_id) |
| `messages` | conversation_messages 总行数 |
| `dependencies` | MySQL / Qdrant（`get_collection`）/ OSS（`get_bucket_info`）健康探活 |

> ⚠️ **已知小瑕疵**：`documents` / `chunks` 计数**未过滤 `deleted_at`**（含软删），与 `/kbs`、`/documents` 列表的过滤行为不一致。监控数字会略偏大。

## 七、设计注记

1. **每个路由模块各自 `_mysql()` 工厂 + 显式 connect/close**——未用 FastAPI 依赖注入连接池（W1 阶段简化）。HybridRetriever 每次检索也独立新建 MySQLStore，避免多跳 asyncio.gather 共享连接池的 close 竞争。
2. **query.py 全局单例**（`_retriever` / `_graph` / `_mysql`）惰性初始化，HybridRetriever 和 LangGraph graph 只建一次。
3. **OSS key 全 ASCII**——规避中文文件名 presigned PUT 签名失配（403）。
4. **Qdrant payload `document_id` 曾误写 `version_id`**（历史 bug 已修），故删除统一按 `version_id` 删 + 按 `kb_id` 兜底。

---

上一篇：[06-知识库生命周期](06-知识库生命周期.md) ｜ 下一篇：[08-前端模块与交互](08-前端模块与交互.md)
