# P0 正确性基线验收(M1 闭环)

> 初版: 2026-11-24 · 闭环更新: 2026-11-25
> 范围: 查询缓存作用域、活动版本/召回过滤、检索候选池与 parent 去重、评测隔离、golden v2 定版与漏斗重跑、CRAG 长尾归因、缓存失效链路、语义缓存命中链路

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

### 基线追踪与生成侧 p95 实验(2026-11-25 第三轮)

- **基线确立**:golden v2 同口径连跑 3 轮聚合([docs/trials/baseline.md](trials/baseline.md)):检索四行指标 **std=0.000**(入库+检索全链路确定性复现);CRAG R@1 0.938±0.010、p50 9474±161ms、p95 16394±1107ms、Faith/Cite 1.000±0.000、3 轮 0 次重试。后续改动前后各跑 N 轮对比此表判回退/增益(`scripts/aggregate_baseline.py`)。
- **模型分级方向排除(预检)**:候选轻量模型热态 3 次中位延迟全部慢于现役 DeepSeek-V4.1-Flash(现役 5.1s vs GLM-5.3-Flash 13.0s / DeepSeek-V4-Flash 10.8s / Qwen3.5-27B 22s),换模型只会更慢。
- **输出封顶 A/B(负结果)**:`CHAT_GENERATE_MAX_TOKENS=512`(trial `20261125_173308`):质量不降反升(R@1=0.963 全配置最高,Faith/Cite 1.000),**但 p95 29.3s 较基线 16.4s 恶化近一倍**——512 截断致 3 问答案被切断 → verifier 判不 grounded → 重试整轮生成放大长尾(长尾 3 问 100% 重试,重试埋点直接证明机制)。**结论:封顶默认关闭**(gateway 能力保留,`chat_generate_max_tokens=0` 为不封顶),输出长度不是安全的长尾杠杆;若复用需更高档位(如 1024)并带重试监控复测。
- **p95 现状结论**:基线 p95≈16s 为 retrieve→grade→generate→verify 多节点 LLM 调用的固有延迟;感知层优化走 `/query/stream`(已具备),服务端无既不降质又压尾的安全杠杆。

### golden v2 定版与长尾归因(2026-11-25 第二轮)

- **golden v2**:10 问 → 54 问(v1 十问原样保留,基线可对比),语料 16 → 48 文档(24 主题 + 24 语义近似干扰项)。语料扩充的必要性:v1 语料 16 文档时 top_k=20 可捞回全库,候选 R@20=1.000 是被语料规模饱和的假信号;48 文档后 top20≈42% 池子,headroom 读数才有意义。每主题至少 2 问,relevant 只允许主题文档,新增跨簇多 relevant 联动问(2~3 doc)。
- **CRAG 长尾埋点**:`eval_crag` 逐问题记录 retry_count/latency/R@5/grounded,输出 retries_total/retried_n 与 tail 列表(lat≥p95 按延迟降序);`run_w2_eval` 报告新增重试列与 p95 长尾归因表,自动判定长尾是否为重试共性问题。
- **语义缓存命中集成脚本**(新增 `scripts/test_semantic_cache.py`):复刻线上 `_generate_cache_key`/`_extract_keywords` 同口径,断言链:原问写精确缓存 → 改写问离线过 overlap≥0.85 阈值 → API 应答逐字节一致(命中) → 命中后无精确键回写 → 跨 kb_ids 不命中(scope 拦截)。
- **依赖声明修复(langchain-openai)**:ModelGateway 经 `init_chat_model("openai:...")` 解析必需该包,此前仅环境实装未在 pyproject 声明(干净环境会缺依赖);golden v2 评测运行实测踩坑后补声明 `>=1.4.0`。

## 验证结果

### 本地(2026-11-25)

| 检查 | 结果 |
|---|---|
| `python -m pytest -q`(默认收集) | 5 passed(收集阶段失败已修复) |
| `python -m pytest tests/unit -q` | 5 passed |
| 修改模块 `py_compile` | passed |
| 修改模块 ruff(E9/F 运行时口径) | passed |
| `git diff --check` | passed |
| golden v2 完整性校验(54问×48文档/relevant全合法/24主题全覆盖) | passed |

### 真实服务集成(2026-11-25)

环境:API(:8000) + MySQL + Redis + Qdrant(1.18.3) + OSS + SiliconFlow embedding/rerank/LLM 全链路可用;评测使用独占 Qdrant 集合,缓存验证使用独占测试 KB(结束即清理,巡检无遗留)。

**漏斗消融(golden v2 定版)** trial=`20261125_114625`([docs/trials/20261125_114625_w2_ablation.md](trials/20261125_114625_w2_ablation.md),54 问 × 48 文档):

| Config | 候选R@5 | 候选R@20 | R@1 | R@5 | MRR@5 | nDCG@5 | Faith★ | Cite★ | p50(ms) | p95(ms) | 重试 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| dense-only | 0.929 | - | 0.815 | 0.929 | 0.870 | 0.879 | - | - | 1945 | 2151 | - |
| +hybrid(RRF) | 0.929 | - | 0.815 | 0.929 | 0.870 | 0.879 | - | - | 2161 | 2763 | - |
| funnel top5→rerank5 | 0.929 | - | 0.889 | 0.929 | 0.917 | 0.912 | - | - | 3379 | 3992 | - |
| funnel top20→rerank5 | 0.929 | 1.000 | 0.889 | 0.935 | 0.924 | 0.914 | - | - | 3790 | 4812 | - |
| +CRAG(kb模式) | - | - | 0.944 | 0.935 | 0.963 | 0.937 | 1.000 | 1.000 | 9421 | 16997 | 0/54 |

读数(54 问定版口径,单次运行):
- **top_k=5 vs 20 裁决**:top20→rerank5 的 R@1 与 top5 打平(0.889),R@5(0.935 vs 0.929)与 MRR@5(0.924 vs 0.917)微胜,代价 p50 +12%/p95 +20%。**v1 smoke 中 top20 R@1 反降(0.700)在 54 问上未复现**,确认该现象是 10 问小样本噪声;线上候选池 top_k=20 配置成立,予以保留。
- **精排增益明确**:两漏斗配置 R@1(0.889)显著高于 dense/hybrid 未精排(0.815);候选池 headroom 真实存在(R@20=1.000 vs 同池前 5 R@5=0.929,48 文档语料下 top_k=20 仅覆盖 42% 池子,不再是 v1 的饱和假象)。
- **CRAG 长尾归因反转**:54 问 **0 次重试**(v1 smoke"p95 长尾来自 agent 重试"的猜想被推翻);p95 17.0s 长尾 3 问(19.0s/18.3s/17.0s)全部 0 重试且 grounded ✓,归因为 LLM 生成节点固有波动(generate 单节点实测最长 12.5s)。若要压 p95,方向在生成侧(流式输出/缩短 prompt/更小生成模型),而非检索侧。
- CRAG 质量上限:R@1=0.944 为全部配置最高(grade 过滤噪声后重排收益),Faith/Cite 双 1.000。
- rerank 服务稳定:两漏斗配置合计 fallback 0/108。
- 可复现性:检索四行指标在两次独立运行(独占集合)中逐位一致(其一为 langchain-openai 缺失中断运行,检索部分完整);v1 smoke trial 20261125_003809 为历史口径(10 问 × 16 文档),已被本 trial 取代,勿再引用其绝对值。

**缓存失效链路** `scripts/test_cache_invalidation.py` PASS:

- generation 链:g0=6 → 7(入库docA) → 8(删docA) → 9(入库docB) → 10(删KB),4 个失效钩子(入库成功/删除前)全部实测生效。
- 首问后当前世代精确键写入 Redis(`query_cache:<md5>`);复问 answer/citations 与缓存逐字节一致(命中证据:LLM 重新生成不可能逐字相同)。
- 删除 docA 后旧世代条目仍在 Redis(TTL 内),但 generation 校验拦截不可命中;完全相同问题+scope 重查返回新事实值 888(旧值 777 未复用),引用由 docA 切换到 docB,新世代缓存重新写入。
- 失效粒度为租户级 generation(已知设计,记录在案)。

**语义缓存命中链路** `scripts/test_semantic_cache.py` PASS(2026-11-25 两轮复验):

- 原问(动态 marker 合金熔点,事实值 777)真实生成后写入当前世代精确键;改写问以线上同口径(复刻 `_extract_keywords` 关键词重叠)离线断言 overlap=0.875 ≥ 0.85 且其精确键不存在。
- 改写问 API 应答的 answer/citations 与原问缓存**逐字节一致**(LLM 重新生成不可能逐字相同,即命中证据);命中后改写问精确键仍不存在(缓存路径直接返回,无回写)。
- scope 隔离:同改写问换空 KB(kb_ids 不同)不命中,语义候选被 filters 全量校验拦截,答案不含 777。
- 已知实现记录在案:语义匹配当前为关键词重叠方案(overlap≥2 且 overlap/max(len)≥0.85,代码内注释标注为简化实现),非向量相似度;升级为嵌入相似度属后续可选优化。

### 本轮发现并处置的环境问题

| 问题 | 处置 |
|---|---|
| qdrant-client 1.12.1 使 hybrid 检索全坏 | 升级 1.19.1 + pyproject 下限 1.14(见上) |
| LLM 代理 juaiapi.com TCP 不可达(阻断 CRAG/问答) | 经确认切换 `.env` LLM 三项至 SiliconFlow(DeepSeek-V4.1-Flash),原配置备份已移出仓库目录(`~/.deeprag_backup/`),`.gitignore` 补 `.env.bak*`/`.zcode/` |
| 本机系统代理 127.0.0.1:7890 偶发抖动 | 环境备注:连接类偶发失败先查代理;Python httpx 会读注册表系统代理(curl 不读) |
| langchain-openai 环境实装但 pyproject 未声明 | 补声明 `>=1.4.0`;踩坑路径:`python -I` 隔离模式移除用户级 site-packages 后 CRAG generate 节点 ImportError,常规模式正常 |
| 无参数 pytest 误收集 scripts/ 集成脚本致收集崩溃 | pyproject 增加 `testpaths=["tests"]`(见 M1 闭环) |

### 遗留 / 后续

- ~~golden v2(≥50 条)定版~~ **已完成**:54 问 × 48 文档定版,top_k=5 vs 20 已裁决(保留 20);后续按同口径多轮运行取均值并追踪基线漂移。
- ~~语义缓存命中的独立集成断言~~ **已完成**(`scripts/test_semantic_cache.py` PASS);可选优化:语义匹配从关键词重叠升级为嵌入相似度(当前实现已在代码与本文档标注为简化方案)。
- CRAG p95 长尾已归因为 **LLM 生成节点固有波动(0 重试)**;若需压 p95,从生成侧入手(流式输出已具备 `/query/stream`、缩短 prompt、更小生成模型),检索侧无需改动。
- LangGraph checkpoint 反序列化告警:`RetrievedDocument` 未注册 msgpack 类型(`LANGGRAPH_STRICT_MSGPACK=true` 将 block),升级 langgraph 前需显式注册 allowed_msgpack_modules,列入升级前检查项。
