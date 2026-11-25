# DeepRAG

基于 LangGraph 的 **Agentic RAG 系统** —— CRAG 纠正式检索（dense+BM25 混合检索 + 加权 RRF + Cross-Encoder 重排 + 自纠状态机），支持 Planner 多跳、父子分块父块展开、多轮对话。

## 技术栈

| 层 | 技术 |
|---|---|
| Agent | LangGraph 1.x LTS（StateGraph + Command 声明式路由 + route 多跳 + MemorySaver） |
| 检索 | BGE-M3 dense（SiliconFlow API）+ Qdrant 原生 BM25 sparse + 加权 RRF + BGE-Reranker（Cross-Encoder） |
| 文档 | Docling 解析 + 父子分块（parent-child chunking） |
| 存储 | MySQL 8.0（元数据/状态/对话）+ 阿里云 OSS（文件/chunks/父块）+ Qdrant（dense+sparse）+ Redis |
| 服务 | FastAPI + Celery（异步入库流水线） |
| LLM | OpenAI 兼容（httpx 直连，不引 langchain） |

## 快速启动

```bash
# 1. 起依赖服务
docker compose up -d

# 2. 初始化MySQL表
python scripts/init_mysql.py

# 3. 安装依赖
pip install --user -e .

# 4. 配置 .env (OSS/Qdrant/MySQL/LLM/embedding 凭据)
copy .env.example .env

# 5. 起API
python -m apps.api.main

# 6. 入库流水线为API进程内异步(BackgroundTasks), 无需单独Worker
#    (原Celery方案因redis-py 8.0 RESP3与远程Redis HELLO不兼容已放弃, 见apps/worker/ingestion.py头注)
```

访问 http://localhost:8000 → `/health` 查依赖状态，`/api/v1/query` 问答。

## 验证脚本

| 脚本 | 验证内容 |
|---|---|
| `scripts/test_connections.py` | MySQL / OSS / Qdrant 连接 |
| `scripts/test_full_ingestion_final.py` | 入库流水线（OSS+MySQL+Qdrant + 真实 embedding） |
| `scripts/test_hybrid_retrieval.py` | dense / sparse-BM25 / RRF 三路对比 + rerank |
| `scripts/test_agent_e2e.py` | CRAG 状态机端到端（带引用答案） |
| `scripts/test_api.py` | `/query` + `/health` |
| `scripts/test_parent_expansion.py` | 父块展开（child 命中 + parent 上下文） |
| `scripts/test_multi_turn.py` | 多轮对话持久化 + 指代理解 |
| `scripts/test_multi_hop.py` | Planner 多跳（子查询分解 + 并行融合） |
| `scripts/run_w2_eval.py` | golden v2 消融+漏斗对照 → `docs/trials/<trial_id>_w2_ablation.{csv,md}` |
| `scripts/test_cache_invalidation.py` | 缓存 generation 失效链路(写→命中→入库/删除bump→不复用) |
| `scripts/test_semantic_cache.py` | 语义缓存命中链路 + kb_ids scope 隔离 |

## 查询示例

```bash
# 单轮
curl -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{"question":"DeepRAG的检索层用了什么技术?"}'

# 多轮 (conversation_id 复用上下文)
curl -X POST http://localhost:8000/api/v1/query \
  -H "Content-Type: application/json" \
  -d '{"question":"它输出多少维?","conversation_id":"sess-1"}'
```

## 文档

- [DESIGN.md](DESIGN.md) — 完整技术方案
- [docs/RESUME_MATERIAL.md](docs/RESUME_MATERIAL.md) — **简历材料**（架构图 + 量化 bullet + 消融数据 + 面试深度叙事）
- [docs/W1](docs/W1_VERIFICATION.md) / [W2](docs/W2_VERIFICATION.md) / [W3](docs/W3_VERIFICATION.md)_VERIFICATION.md — 三阶段验收报告
- [docs/w2_ablation.csv](docs/w2_ablation.csv) — 消融原始数据
