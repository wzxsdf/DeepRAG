# P0 正确性基线验收(M1 闭环)

> 初版: 2026-11-24 · 闭环更新: 2026-11-25
> 范围: 查询缓存作用域、活动版本/召回过滤、检索候选池与 parent 去重、评测隔离、真实服务漏斗对照、缓存失效链路

## 已完成(代码)

- 查询缓存增加租户 generation。知识库入库成功和文档删除前提升 generation,旧精确缓存和语义缓存候选均不可继续命中。
- 语义缓存命中增加 generation、tenant、kb_ids、mode 全量 scope 校验,避免跨租户、跨知识库和跨模式污染。
- Qdrant dense/sparse 两路共用 tenant、kb、ACL、active version 过滤条件。ACL 标签按每个标签分别加入 must 条件。
- `RetrievalFilter` 增加多版本 `active_version_ids`,支持多知识库查询。数据库没有活动指针时回退到已索引最新版本;明确无可用版本时使用不可能匹配的版本条件,避免空列表放宽为全量召回。
- 入库成功路径按"索引写入 → 版本 INDEXED → active pointer 切换 → 缓存失效 → SUCCESS"执行;active pointer 切换失败进入 FAILED。
- 文档和知识库删除复用租户级缓存失效钩子。
- 检索后精排候选池统一扩大到 20,Cross-Encoder 前按 parent_id 去重,失败回退也保持去重。
- KB 模式、ReAct 工具、Plan Agent 和旧 Agent 工具路径均使用统一候选池常量。

### M1 闭环新增(2026-11-25)

- **评测隔离**:`QdrantWriter` 支持可选 collection name(线上默认 `chunks` 不变);`src/evaluation/ablation.py` 与 `scripts/run_w2_eval.py` 每次运行创建独占 `eval_<uuid>` 集合,dense/hybrid/rerank/CRAG 共用同一评测集合,结束仅清理本次集合(非 eval_ 前缀拒绝删除);异常路径也会清理(finally)。
- **漏斗对照指标**:`eval_retrieval` 四配置(dense / hybrid / top5→rerank5 / top20→rerank5),输出候选 R@5、候选 R@20、R@1、R@5、MRR@5、nDCG@5、p50/p95、rerank fallback;`run_w2_eval` 报告写入 `docs/trials/<trial_id>_w2_ablation.{md,csv}`(不覆盖历史基线),结论由当次数据动态生成并标注 smoke 样本量限制。
- **缓存失效集成脚本**(新增 `scripts/test_cache_invalidation.py`):运行时动态生成内容可区分的 marker PDF(事实值 777/888,置于文本前 150 字符内以匹配 kb 模式证据截断),走真实 `/api/v1/query`,断言链:首问写精确缓存 → 复问命中(answer/citations 逐字一致)→ 删文档/入库 bump → 同问重查旧缓存不复用 → 删 KB bump;失败路径 finally 兜底删除测试 KB,不留孤儿数据。
- **pytest 默认收集修复**:`pyproject.toml` 增加 `[tool.pytest.ini_options] testpaths=["tests"]`;`scripts/` 下独立脚本(顶层 asyncio.run + 外部服务依赖)不再被无参数 pytest 误收集。
- **依赖修复(qdrant-client)**:1.12.1 → 1.19.1,`pyproject.toml` 下限提至 `>=1.14.0`。原因:`query_points` 对 `Qdrant/bm25` 的服务端推理透传需 client ≥1.14,1.12 误走本地 fastembed 报 `not among supported models`,导致 **hybrid(BM25 路)检索整体不可用**(P0 级问题);1.20 对 server 1.18.3 有 minor 差 2 的兼容警告,故固定 1.19。
- **eval_crag 显式 `mode="kb"`**:缺省 mode=quick 会使 graph 进入 tool_agent(联网工具检索),与检索消融口径不可比;修复前 CRAG 行全 0(trial 20261125_002736,已删除该误导性产物)。

## 验证结果

### 本地(2026-11-25)

| 检查 | 结果 |
|---|---|
| `python -m pytest -q`(默认收集) | 5 passed(收集阶段失败已修复) |
| `python -m pytest tests/unit -q` | 5 passed |
| 修改模块 `py_compile` | passed |
| 修改模块 ruff(E9/F 运行时口径) | passed |
| `git diff --check` | passed |

### 真实服务集成(2026-11-25)

环境:API(:8000) + MySQL + Redis + Qdrant(1.18.3) + OSS + SiliconFlow embedding/rerank/LLM 全链路可用;评测使用独占 Qdrant 集合,缓存验证使用独占测试 KB(结束即清理,巡检无遗留)。

**漏斗消融** trial=`20261125_003809`([docs/trials/20261125_003809_w2_ablation.md](trials/20261125_003809_w2_ablation.md),golden 10 问 smoke):

| Config | 候选R@5 | 候选R@20 | R@1 | R@5 | MRR@5 | nDCG@5 | Faith★ | Cite★ | p50(ms) | p95(ms) |
|---|---|---|---|---|---|---|---|---|---|---|
| dense-only | 0.900 | - | 0.800 | 0.900 | 0.850 | 0.861 | - | - | 2696 | 3030 |
| +hybrid(RRF) | 0.900 | - | 0.800 | 0.900 | 0.850 | 0.861 | - | - | 2679 | 4577 |
| funnel top5→rerank5 | 0.900 | - | 0.900 | 0.900 | 0.900 | 0.869 | - | - | 4910 | 5475 |
| funnel top20→rerank5 | 0.900 | 1.000 | 0.700 | 0.850 | 0.783 | 0.785 | - | - | 4916 | 5057 |
| +CRAG(kb模式) | - | - | 0.800 | 0.850 | 0.850 | 0.826 | 0.900 | 0.900 | 10457 | 33904 |

读数(均为 smoke 口径,不作为对外数字):
- rerank 对 top-1 精度增益明确:R@1 0.800 → 0.900。
- 候选池扩大上限 headroom 存在(R@20=1.000 vs R@5=0.900),但 top20→rerank5 在 10 问上最终 R@1 反降(0.700):大候选池引入精排噪声 vs 小样本噪声,留待 golden v2(≥50 条)按同口径判定 top_k 取舍。
- CRAG 端到端(kb 模式)检索质量与 top5→rerank5 相当,R@5=0.850,换来自纠循环与 grounded 生成(Faith=0.900);p95 33.9s 长尾来自个别问题的 agent 重试。
- 检索漏斗四行数字在三轮独立运行中逐位复现(20261125_000646 / 003809 等),基线可复现性达标。

**缓存失效链路** `scripts/test_cache_invalidation.py` PASS:

- generation 链:g0=6 → 7(入库docA) → 8(删docA) → 9(入库docB) → 10(删KB),4 个失效钩子(入库成功/删除前)全部实测生效。
- 首问后当前世代精确键写入 Redis(`query_cache:<md5>`);复问 answer/citations 与缓存逐字节一致(命中证据:LLM 重新生成不可能逐字相同)。
- 删除 docA 后旧世代条目仍在 Redis(TTL 内),但 generation 校验拦截不可命中;完全相同问题+scope 重查返回新事实值 888(旧值 777 未复用),引用由 docA 切换到 docB,新世代缓存重新写入。
- 失效粒度为租户级 generation(已知设计,记录在案)。

### 本轮发现并处置的环境问题

| 问题 | 处置 |
|---|---|
| qdrant-client 1.12.1 使 hybrid 检索全坏 | 升级 1.19.1 + pyproject 下限 1.14(见上) |
| LLM 代理 juaiapi.com TCP 不可达(阻断 CRAG/问答) | 经确认切换 `.env` LLM 三项至 SiliconFlow(DeepSeek-V4.1-Flash),原配置备份于 `.env.bak-juaiapi` |
| 本机系统代理 127.0.0.1:7890 偶发抖动 | 环境备注:连接类偶发失败先查代理;Python httpx 会读注册表系统代理(curl 不读) |

### 遗留 / 后续

- golden v2(≥50 条)定版后按同口径重跑,替换 smoke 数字并判定 top_k=5 vs 20(P1 内容轨并行推进)。
- 语义缓存命中的独立集成断言本轮未覆盖(generation 失效对语义候选的保护已由代码路径核对 + 同世代精确键断言间接覆盖),可在 P2 judge/指标基建时补。
- CRAG p95 长尾(单问 agent 重试)待 golden v2 观察是否为共性问题。
