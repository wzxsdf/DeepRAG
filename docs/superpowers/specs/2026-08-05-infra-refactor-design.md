# DeepRAG 基础设施分层重构设计（infra-refactor）

- **日期**: 2026-08-05
- **状态**: 待评审
- **范围**: 引入 infra 层（集中配置 + ModelGateway + 依赖装配）、领域模型层、Repository 层；解耦存储/检索/API；统一 LLM 调用
- **动机**: 现状 infra 薄弱（配置散落 166 处 `os.getenv`、无依赖装配、无领域模型、API 内联 SQL、存储实现硬编码耦合、LLM 两套调用）。详见前序评审

---

## 1. 目标 / 非目标

**目标：**
- **集中配置**：`pydantic-settings` 统一 Settings，消除散落的 `os.getenv`
- **ModelGateway**：`init_chat_model` 解析 `purpose→model`，统一 chat/embed/rerank；CRAG 的 httpx 与 create_agent 的 ChatOpenAI 收敛到一处；多厂商多模型配置驱动
- **依赖装配**：FastAPI lifespan 初始化共享 resources + 连接池，`Depends` 注入；消除全局可变单例与每次 `new+connect+close`
- **领域模型 + Repository**：业务对象有领域类，SQL 下沉到 Repository，API 层变薄
- **检索层解耦**：`HybridRetriever` 构造注入存储实现（依赖倒置）

**非目标（明确不在范围）：**
- 不重写已有且稳定的业务逻辑（CRAG 自纠、plan_agent、mode 路由行为不变）
- 不换存储引擎（仍 MySQL/Qdrant/OSS）
- 不引入 ORM（用手写 SQL + Repository 封装，足够轻量）
- 不一次性迁移所有 API 端点（按阶段渐进，每阶段可回归）

## 2. 目标分层架构

```
apps/api/            表现层: 路由 + Pydantic DTO(薄, 只做HTTP适配 + Depends注入)
apps/worker/         入库 worker(注入 settings/resources)
src/
  core/              ★ 新增 infra 层
    config.py        Settings(pydantic-settings): DB/Qdrant/OSS/API + LLMSettings
    llm.py           ModelGateway: chat_model(purpose) + embedder + reranker
    lifespan.py      FastAPI lifespan: 初始化 resources(mysql/qdrant/oss/gateway) + 连接池
    container.py     依赖容器(AppResources 单例 + 工厂)
    logging.py       structlog 配置
  domain/            ★ 新增领域层
    models.py        Document / KB / DocumentVersion / IndexingJob / Message (Pydantic)
  storage/
    protocol.py      接口(已有, 扩展 Repository 接口 + Embedder/Reranker)
    mysql/qdrant/oss 实现(构造接受 settings 注入, 不自读 env)
    repositories/    ★ 新增: DocumentRepository / KBRepository (封装SQL, 返领域模型)
  retrieval/         HybridRetriever(构造注入 qdrant/oss/mysql/gateway)
  agents/            graph/tools(注入 gateway.chat_model)
  security/          (不动)
```

## 3. 详细设计

### 3.1 集中配置 `core/config.py`

用 `pydantic-settings` 的 `BaseSettings`，一处定义、类型校验、环境隔离：

```python
from pydantic_settings import BaseSettings, SettingsConfigDict

class ProviderCfg(BaseModel):
    api_key: str = ""
    base_url: str = ""

class LLMSettings(BaseModel):
    # purpose → "provider:model" 字符串(init_chat_model 解析); 多厂商靠前缀, 一家多模型靠不同purpose
    chat_generate: str = "openai:gpt-4o"       # CRAG 生成(强)
    chat_fast:     str = "openai:gpt-4o-mini"  # grade/route/judge(快省)
    chat_reason:   str = "openai:gpt-4o"       # plan 规划(推理)
    embed:         str = "siliconflow:BAAI/bge-m3"
    rerank:        str = "bge-reranker-v2-m3"
    providers: dict[str, ProviderCfg] = {       # provider连接信息, 按前缀分组
        "openai": ProviderCfg(base_url="https://www.juaiapi.com/v1"),
        "siliconflow": ProviderCfg(base_url="https://api.siliconflow.cn/v1"),
    }

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_nested_delimiter="__", extra="ignore")
    mysql: MySQLCfg = ...
    qdrant: QdrantCfg = ...
    oss: OSSCfg = ...
    llm: LLMSettings = LLMSettings()
    api_host: str = "0.0.0.0"; api_port: int = 8000

_settings = None
def get_settings() -> Settings:
    global _settings
    if _settings is None: _settings = Settings()
    return _settings
```

所有类改为 `__init__(self, cfg: XxxCfg)` 接受注入，消除自身 `os.getenv`。

### 3.2 ModelGateway `core/llm.py`

统一 LLM 入口。`init_chat_model`（langchain 原生）解析 `provider:model` 字符串 → `BaseChatModel`，按 purpose 缓存：

```python
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

class ModelGateway:
    """统一模型入口: purpose→BaseChatModel, 多厂商多模型配置驱动.
    CRAG(chat/chat_json)与create_agent都从此取model, 消除httpx手写+ChatOpenAI两套调用."""
    def __init__(self, llm_cfg: LLMSettings):
        self._cfg = llm_cfg
        self._cache: dict[str, BaseChatModel] = {}

    def chat_model(self, purpose: str) -> BaseChatModel:
        if purpose not in self._cache:
            model_str = getattr(self._cfg, purpose)  # e.g. "openai:gpt-4o-mini"
            provider = model_str.split(":", 1)[0]
            pcfg = self._cfg.providers.get(provider, ProviderCfg())
            self._cache[purpose] = init_chat_model(
                model=model_str, api_key=pcfg.api_key or None, base_url=pcfg.base_url or None,
                temperature=0)
        return self._cache[purpose]

    def embedder(self) -> "Embedder": ...   # 实现 Embedder Protocol
    def reranker(self) -> "Reranker": ...
```

**统一 chat 调用**：`agents/llm.py` 的 `chat/chat_json/chat_stream` 改为基于 `gateway.chat_model(purpose).ainvoke(...)`。httpx 直连退役。`chat_json` 用 `with_structured_output` 或 `response_format`（BaseChatModel 原生支持）。create_agent 的 `get_react_agent` 改用 `gateway.chat_model("chat_fast")`。

**Embedder/Reranker 接口**（`storage/protocol.py` 扩展）：
```python
class Embedder(Protocol):
    async def embed_query(self, text: str) -> list[float]: ...
class Reranker(Protocol):
    async def rerank(self, query: str, docs: list, top_n: int) -> list: ...
```
现有 `embedder.embed_query`/`reranker.rerank` 包装为实现，配置驱动。

### 3.3 依赖装配 `core/lifespan.py` + `container.py`

FastAPI lifespan 启动时初始化共享 resources（带连接池），停止时关闭：

```python
@dataclass
class AppResources:
    settings: Settings
    mysql: MySQLStore          # 共享实例(内部连接池)
    qdrant: QdrantWriter
    oss: OssStore
    gateway: ModelGateway
    retriever: HybridRetriever
    # graph 按需构建(注入 retriever/gateway)

async def lifespan(app: FastAPI):
    s = get_settings()
    res = AppResources(settings=s,
        mysql=MySQLStore(s.mysql), qdrant=QdrantWriter(s.qdrant),
        oss=OssStore(s.oss), gateway=ModelGateway(s.llm), ...)
    await res.mysql.connect()  # 建池
    app.state.resources = res
    yield
    await res.mysql.close()
```

API 层用 `Depends` 取 resources：
```python
def get_resources(request: Request) -> AppResources:
    return request.app.state.resources

@router.get("/documents")
async def list_docs(res: AppResources = Depends(get_resources), ...):
    docs = await res.document_repo.list(kb_id=...)  # 返领域模型
    return [DocumentDTO.from_model(d) for d in docs]
```

消除：`query.py` 全局 `_retriever/_graph/_mysql`、`documents.py` 模块级 `oss_store`、每端点 `_mysql()` 手动 `new+connect+close`。

### 3.4 领域模型 `domain/models.py`

```python
class DocumentVersion(BaseModel): id: str; document_id: str; status: str; oss_key: str; ...
class Document(BaseModel): id: str; kb_id: str; title: str; versions: list[DocumentVersion]; ...
class KnowledgeBase(BaseModel): id: str; name: str; status: str; doc_count: int
class IndexingJob(BaseModel): id: str; version_id: str; stage: str; status: str; error_msg: str | None
```

### 3.5 Repository 层 `storage/repositories/`

封装 SQL，返回领域模型。API 层不再写 SQL：
```python
class DocumentRepository:
    def __init__(self, mysql: MySQLStore): self.mysql = mysql
    async def list(self, kb_id: str | None) -> list[Document]: ...   # 现documents.py的JOIN SQL下沉
    async def get(self, document_id: str) -> Document | None: ...
    async def create_version(self, ...) -> DocumentVersion: ...
```

### 3.6 检索层注入

`HybridRetriever.__init__(self, qdrant, oss, mysql_factory, gateway)` —— 接受注入，不再自 `new` + 自读 `os.getenv`。`embed_query` 改 `gateway.embedder().embed_query()`。

## 4. 阶段划分（每阶段独立可验证）

每阶段结束跑 `test_modes`/`test_tool_calling`/`test_plan_agent` 全量回归，行为不变。

| 阶段 | 内容 | 主要文件 | 风险 |
|---|---|---|---|
| **1. 集中配置** | `core/config.py` Settings；所有存储类构造接受 cfg 注入（先保留 os.getenv 兜底，渐进替换） | config.py + storage/* + retrieval/hybrid + embedder + llm.py | 低（机械替换） |
| **2. ModelGateway + 统一 LLM** | `core/llm.py`；CRAG 的 chat/chat_json 改 BaseChatModel；create_agent 改用 gateway；httpx 退役；embed/rerank 接口化 | core/llm.py + agents/llm.py + tools.py + embedder + reranker | 中（动 CRAG 核心，重点回归） |
| **3. 依赖装配** | `core/lifespan.py` + `container.py`；FastAPI lifespan；Depends 注入；删全局单例 + 模块级 new | core/lifespan + main.py + 所有 api/* | 中（改装配链） |
| **4. 领域模型 + Repository** | `domain/models.py` + `storage/repositories/`；SQL 下沉；API 层变薄 | domain/ + repositories/ + api/documents,api/kb | 中（动 API） |
| **5. 检索注入 + 可观测** | HybridRetriever 构造注入；接 structlog 替换 print；接 langfuse | retrieval/hybrid + 各处 print | 低 |

**建议从阶段 1 开始**（低风险、是后续基础）。每阶段一个独立 plan。

## 5. 兼容性与回归

- **行为不变**：CRAG 自纠、mode 路由、plan_agent、工具调用行为完全保留，只换"怎么拿配置/怎么拿 model/怎么拿连接"。
- **`.env` 兼容**：Settings 的字段名与现有 env key 对齐（`env_nested_delimiter="__"` 支持新分组，旧扁平 key 用 alias 兼容）。
- **回归门禁**：每阶段必须 `test_modes`(7) + `test_tool_calling`(5) + `test_plan_agent`(2) 全过才进下一阶段。
- **测试注入收益**：阶段 3 后，单元测试可注入 mock resources（当前无法，因全局单例 + 硬编码连接）。

## 6. 设计决策记录

1. **provider 适配用 `init_chat_model`**（非 litellm Router）：langchain 原生，create_agent 已用 BaseChatModel 天然一致；多厂商靠 provider 前缀。litellm 留作后续 fallback/负载均衡时引入。
2. **LLM 全部统一到 BaseChatModel**（含 CRAG）：消除 httpx + ChatOpenAI 两套调用。代价是动 CRAG 核心（阶段 2 重点回归）。
3. **不用 ORM**：Repository 封装手写 SQL + 返领域模型，足够轻量（ponytail）。
4. **分 5 阶段渐进**：每阶段独立可验证，避免大爆炸式重构。
5. **Settings 兼容旧 .env**：避免迁移期配置断裂。
