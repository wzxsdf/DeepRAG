# 后端 API 与存储栈

> 后端全景 + infra 分层架构（阶段 1-5 重构成果）。涉及 `apps/api/*`、`src/core/*`、`src/domain/*`、`src/storage/*`、`src/security/auth.py`。

## 一、分层架构（infra 重构后）

DeepRAG 后端采用**严格分层**，消除早期"API 内联 SQL + 全局单例 + 胖存储类 + 两套 LLM 调用"的耦合：

```
┌─────────────────────────────────────────────────────┐
│  表现层 apps/api/ (FastAPI 路由)                      │
│  端点变薄: 只编排(调Repository/Service), 不写SQL       │
├─────────────────────────────────────────────────────┤
│  core/ (基础设施层)                                   │
│  config.py   集中配置(pydantic-settings, 消除166处os.getenv)│
│  llm.py      ModelGateway(init_chat_model 统一LLM)    │
│  lifespan.py 依赖装配(AppResources + Depends注入)     │
│  logging.py  structlog(替代print)                     │
├─────────────────────────────────────────────────────┤
│  domain/ (领域层)                                     │
│  models.py   领域模型 dataclass(User/KB/Document/...)  │
├─────────────────────────────────────────────────────┤
│  storage/ (存储层)                                    │
│  repositories.py  8个Repository(手写SQL, 返领域模型)   │
│  mysql.py     纯执行层(execute/fetch/fetchrow/事务)   │
│  qdrant_store.py / oss.py  向量/对象存储              │
│  protocol.py  Retriever/ObjectStore 协议              │
├─────────────────────────────────────────────────────┤
│  agents/ (Agent层)  retrieval/ (检索层)               │
└─────────────────────────────────────────────────────┘
```

**设计原则**：依赖单向向下（表现层 → core/domain → storage）；Repository 返领域模型（告别裸 dict）；MySQLStore 瘦身为纯执行层；LLM 经 ModelGateway 统一入口。

## 二、应用入口 + lifespan 依赖装配

**`apps/api/main.py`**：`FastAPI(lifespan=lifespan)`，启动时 `setup_logging()` + 建共享 `AppResources`，挂载 6 个子路由 + CORS。

**`src/core/lifespan.py`** —— infra 阶段 3 的核心，取代旧"全局单例 + 每端点 connect/close"：

```python
@dataclass
class AppResources:
    settings: Settings
    mysql: MySQLStore       # 共享连接池(autocommit, 每次execute独立连接)
    qdrant: QdrantWriter
    oss: OssStore
    gateway: ModelGateway   # 统一LLM入口
    retriever: HybridRetriever
    graph: Any              # compiled langgraph

async def lifespan(app):
    s = get_settings()
    mysql = MySQLStore(cfg=s.mysql_cfg); await mysql.connect()
    await UserRepository(mysql).ensure_schema()       # 收敛运行时DDL(users补列)
    await ConversationRepository(mysql).ensure_schema()  # (conversation_messages建表)
    gateway = get_gateway()                           # ModelGateway初始化
    retriever = HybridRetriever(qdrant=..., oss=..., mysql=mysql, settings=s)
    app.state.resources = AppResources(...)
    yield
    await mysql.close()
```

**端点经 `Depends` 取共享 resources**（`get_mysql` / `get_qdrant` / `get_oss` / `get_graph` / `get_resources`），不再每端点 new + connect + close。

> ⚠️ 测试须 `with TestClient(app)` 触发 lifespan（否则 `app.state.resources` 未设，Depends 失败）。

## 三、API 路由清单（端点变薄，经 Repository）

| 模块 | 端点 | 鉴权 |
|---|---|---|
| **auth** | POST `/register` `/login`、GET `/me` | register/login 公开 |
| **kbs** | POST `/kbs`、GET `/kbs`、PATCH `/kbs/{id}`、DELETE `/kbs/{id}` | 需登录 |
| **documents** | POST `/uploads/presign` `/documents/confirm`、GET `/documents` `/{id}` `/{id}/preview`、DELETE `/{id}` | 需登录 |
| **query** | POST `/query`（同步）、POST `/query/stream`（SSE 流式） | token 可选(匿名降级 default) |
| **conversations** | GET `/conversations`、GET `/conversations/{id}/messages` | 需登录 |
| **monitor** | GET `/metrics` | 需登录 |

**端点职责**：只编排（调 Repository / 跨存储函数），不写 SQL。例：

```python
@router.get("/documents")
async def list_documents(kb_id: str | None = None, mysql: MySQLStore = Depends(get_mysql)):
    docs = await DocumentRepository(mysql).list_active(kb_id)   # 返领域模型
    return [{"id": d.id, "title": d.title, ...} for d in docs]
```

## 四、core 基础设施层

### 4.1 config.py（阶段 1：集中配置）

`Settings(BaseSettings)` 扁平字段对齐 `.env` + `MySQLCfg`/`QdrantCfg`/`OSSCfg`/`ProviderCfg` dataclass + `*_cfg` / `providers` property + `get_settings()` 单例。消除散落 166 处 `os.getenv`。

### 4.2 llm.py（阶段 2：ModelGateway）

`ModelGateway.chat_model(purpose)` 用 `init_chat_model` 解析 `"provider:model"` → `BaseChatModel`，按 purpose 缓存；`providers[provider]` 取 base_url/api_key。**消除 httpx 手写 + ChatOpenAI 两套 LLM 调用**。

- purpose 路由：`generate`（CRAG 生成）/ `fast`（grade/judge/rewrite）/ `reason`（plan 规划）
- 字段空 → 回退 `openai:{litellm_model}` 兼容现有单端点
- langfuse：三 key 齐全则建 `CallbackHandler` bind 到 model（代码就绪，填 key 即启用 trace）

> 详见 [03-混合检索与CRAG生成](03-混合检索与CRAG生成.md) 的 LLM 调用部分。

### 4.3 logging.py（阶段 5：structlog）

`setup_logging()` + `log = structlog.get_logger()`（ConsoleRenderer 彩色），替代后端 `print`。

## 五、domain 领域层

**`domain/models.py`**：领域模型 dataclass，给 Repository 类型化契约：

- 实体：`User` / `KnowledgeBase` / `Document` / `DocumentVersion` / `IndexingJob` / `ChunkMeta` / `ConversationMessage`
- DTO（JOIN/聚合）：`KBWithCount` / `DocumentListItem` / `DocumentDetail` / `ConversationSummary`

## 六、storage 存储层

### 6.1 mysql.py（阶段 4：瘦身为纯执行层）

删除 12 个领域方法（搬入 Repository），只留：`execute` / `fetch` / `fetchrow` / `execute_transaction`（事务级联）/ `connect` / `close`。autocommit，每次 execute 独立连接（行为与旧每端点 new 一致）。

### 6.2 repositories.py（阶段 4：Repository 层）

8 个 Repository 单文件，**手写 SQL（不用 ORM）**，返领域模型：

| Repository | 关键方法 |
|---|---|
| `UserRepository` | `ensure_schema` / `find_by_email` / `create` |
| `KBRepository` | `create` / `list_with_doc_count` / `get` / `find_including_deleted` / `rename` / `set_status` / `soft_delete` |
| `DocumentRepository` | `create` / `list_active` / `get_detail` / `find_for_delete` / `purge_with_versions`(事务级联) / `count` |
| `DocumentVersionRepository` | `create` / `get_ids` / `get_oss_keys` / `latest_oss_key` / `update_status` |
| `IndexingJobRepository` | `create` / `update` / `update_by_version`(worker用) / `count_by_status` |
| `ChunkRepository` | `insert` / `get_parent_ids` / `count` |
| `ConversationRepository` | `ensure_schema` / `save_message` / `load_history` / `list_conversations` / `get_messages` / `count_*` |

Repository 无状态（只持 mysql 引用），端点内 `DocumentRepository(mysql)` 临时构造（轻量）。

### 6.3 表结构概要

| 表 | 关键字段 | 生命周期字段 |
|---|---|---|
| `tenants` | id, name | — |
| `users` | id, tenant_id(FK), email(UNIQUE), name, password_hash | — |
| `knowledge_bases` | id, tenant_id, name, embedding_model, config_json | **status**, **deleted_at**, **deleted_by** |
| `documents` | id, kb_id(FK), title | **deleted_at**, **deleted_by** |
| `document_versions` | id, document_id(FK), oss_key, sha256, file_size, status | (物理删) |
| `chunks` | id, version_id(FK), parent_id, page, heading_path, content_hash, oss_key | (物理删) |
| `indexing_jobs` | id, version_id(FK), stage, status, retry_count, error_msg | (物理删) |
| `conversation_messages` | conversation_id, user_id, role, content, created_at | — |

**入库状态机**：`document_versions.status` UPLOADED→PARSING→INDEXING→INDEXED/FAILED；`indexing_jobs.status` PENDING→RUNNING→SUCCESS/FAILED。

> ⚠️ **schema 漂移（阶段 4 未纳入迁移系统，仅收敛 DDL）**：
> - `users` 的 `name`/`password_hash`、`conversation_messages` 表由 `Repository.ensure_schema()` 在 lifespan 启动时补建（`mysql_schema.sql`/`init_mysql.py` 未同步，属已知技术债）
> - schema.sql 的 `conversations` + `messages` 两表是死表，运行时实际用单表 `conversation_messages`

## 七、三存储分工

| 存储 | 角色 | 存什么 | 关键特性 |
|---|---|---|---|
| **MySQL** | 元数据真相源 | KB/文档/版本/chunk 元数据、jobs、会话消息、软删审计 | 事务（`execute_transaction`）、软删字段 |
| **Qdrant** | 检索引擎 | collection `chunks`：dense(1024) + sparse(BM25) + payload | 服务端 RRF 融合、按 payload Filter 删除 |
| **OSS** | 文件存储 | 原始 PDF / parsed JSON / chunk 文本 / parent 块 | 预签名上传、按 prefix 批量删 |

**关联键**：`version_id` 贯穿三存储（MySQL `document_versions.id`、Qdrant payload、OSS key 前缀），删除时跨存储精确定位。

## 八、JWT 鉴权

**`src/security/auth.py`**：passlib bcrypt 哈希 + python-jose 签发（HS256，24h）。`oauth2_scheme = OAuth2PasswordBearer(auto_error=False)` 让 query 接口可不带 token（降级 default 用户，匿名友好）。`get_current_user` 解 token → 返 `{user_id, tenant_id, email, name}`。

## 九、监控 Metrics（经 Repository）

**GET `/metrics`** 返回：`jobs`（按 status 分组）、`documents`/`chunks`/`conversations`/`messages` 计数、`dependencies`（MySQL/Qdrant/OSS 健康探活）。计数经各 Repository 的 `count*` 方法。

## 十、设计注记

1. **Depends 注入共享 resources**（阶段 3）—— 取代旧"每端点 `_mysql()` 工厂 + connect/close"。lifespan 建共享 MySQLStore 池 + QdrantWriter + OssStore + HybridRetriever + graph，所有端点经 Depends 取。
2. **Repository 返领域模型**（阶段 4）—— API 层告别裸 dict 操作，类型化契约；跨存储编排（`purge_document`/`delete_kb`）保留模块函数（不抽 Service，YAGNI），内部调 Repository。
3. **worker 纳入 Repository**（阶段 4）—— `ingestion_core`/`ingestion` 改用 ChunkRepository/VersionRepository/JobRepository（worker 仍 os.getenv 建连接，未接 config，属后续可选）。
4. **OSS key 全 ASCII**——规避中文文件名 presigned PUT 签名失配（403）。
5. **Qdrant payload `document_id` 曾误写 `version_id`**（历史 bug 已修），删除统一按 `version_id` 删 + 按 `kb_id` 兜底。

---

上一篇：[06-知识库生命周期](06-知识库生命周期.md) ｜ 下一篇：[08-前端模块与交互](08-前端模块与交互.md)
