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

### 拒答校准 + hybrid定案 + 端点分数漂移发现(2026-11-27 第七轮)

**① 拒答校准落地与净效应**(用户决策: ②校准 ①hybrid扫参 记忆封存, RAG检索优先):
- 标定: golden 32问top1中位0.927 vs 不可答10问中位0.012, 双峰分布0.03~0.8有空隙 → `retrieval_min_score=0.30`(env可调) + GRADER prompt收紧("话题相关但不含答案=false")。
- 不可答验证: 诚实降级 0.80→0.90, 特斯拉幻觉案例修复(原kb_grounded编造→tool诚实作答); 残留1例为"pgvector vs Qdrant"半可答边界(语料确有Qdrant文档)。
- **净效应是质量提升而非回退**(trial `20261127_120758` vs 基线`20261126_202308`): judge_answer **0.839→0.885~0.903**(两次完整测量稳定抬升) — 基线中5问"答案错却自称grounded"(如[2][11][46][48][51], verifier自洽虚高), 阈值使其诚实降级到通用知识兜底, judge判兜底答案10/12正确; grounded 0.984→0.836~0.855 = 去水分后的真实标签。

**② hybrid定案**(RRF扫参, 5组权重×62问): sparse权重单调恶化, 无任何配置赢dense — 0.15与纯dense逐位持平, 0.35(旧默认)-1.6pt, 0.5/0.7 -3.2pt。**生产默认改0.85/0.15**(≈dense实证最优, 稀疏路保留应对真实长文档语料, `RETRIEVAL_SPARSE_WEIGHT`可调); README头号卖点如实改写"dense+Cross-Encoder精排", hybrid降级为"已实现已消融无增益"。

**③ 一级发现: SiliconFlow rerank分数跨时间窗漂移**。同一问答对在昨日20:23窗口rerank分数0.9+(如[12]口语问→doc_eval R@1=1.0), 夜间/次日窗口同对0.1x(12:05管线追踪实测), 而高词汇重叠对(jieba)两窗口均0.88——疑似服务端模型版本变动, 边界(口语改写型)对分数翻转, 简单对稳定。影响: CRAG recall@1 0.952(基线窗)↔0.806(当前窗)的主要来源; grounded/faith 同理。
- 防御四件套已落地: rerank全低分(<0.30与阈值同线)自愈重试; sync/async权重对齐; ckpt按idx去重; **坏窗口熔断**(≥8问且可疑率≥75%即中止, ckpt保留换窗续跑)。
- **窗口指纹预检**: 固定参考对(jieba)打分入trial(本次0.881), 跨窗trial可解释可比。
- 深夜运维连带发现并修复: 后台长跑~21min静默死亡(防待机+分块断点续跑ckpt, 62问分块完成), DNS/SSH闪断(网络探测等待), 卡死进程无超时挂起(识别PID精确清理)。

**基线重立(trials=3)**: `20261126_202308`(基线参考, 健康窗CRAG+judge) + `20261126_220012`(新权重漏斗) + `20261127_120758`(当前窗完整) → baseline.md; hybrid漏斗行std=0.009如实反映扫参前后两配置(0.823/0.839)。无效窗口/旧配置trial移入 `docs/trials/invalid/`(010659坏窗, 205901/214329旧权重混窗, 201739孤儿), 不参与聚合。

**单元回归**: pytest 48 passed(+阈值短路单测); 窗口指纹/judge smoke/子集对照脚本入 `scripts/`。

**遗留(下一阶段候选)**: reranker对口语改写型问法的真实短板(12/62问无法置信度>0.3命中证据 — kb模式query预改写或本地reranker候选); "pgvector vs Qdrant"半可答边界; P3多租户(用户已确认要做); Agent/记忆优化阶段(记忆已封存待A/B重评)。

### 评测效度补齐 P1②: golden v3 + 独立judge + 负样本(2026-11-26 第六轮)

**golden v3**(62问×48文档 + UNANSWERABLE 10问, `src/evaluation/golden.py`): v2的54问原样保留并全部补 `answer` 参考答案; 新增8问关键词型(RRF/IDF/InnoDB/BGE-M3等ASCII精确token — BM25稀疏路理论主场, v2只有改写型查询是"hybrid增益无数据"的根因); 10问不可答负样本(语料无答案, 部分含tesla/gpt/k8s干扰检索噪声)。

**独立judge**: `CHAT_JUDGE_MODEL=openai:zai-org/GLM-5.3-Flash`(与生成端DeepSeek-V4.1-Flash异族异构; Qwen3.5-9B实测27.9s/次过慢弃用), gateway新增judge purpose, `chat_json`支持purpose参数; 对照参考答案评answer_correct/grounded/citation_correct, 替代自评1.000的无区分度口径。

**完整trial** `20261126_202308`([md](trials/20261126_202308_w2_ablation.md), 分块断点续跑完成; 漏斗数字与另两次独立运行逐位一致—确定性三连复现):

| Config | R@1 | R@5 | MRR@5 | nDCG@5 | p50 | p95 | Faith★自评 | Judge答准★ | Judge支撑★ | Judge引用★ |
|---|---|---|---|---|---|---|---|---|---|---|
| dense-only | 0.839 | 0.938 | 0.887 | 0.895 | 1.0s | 1.2s | - | - | - | - |
| +hybrid(RRF) | 0.823 | 0.938 | 0.879 | 0.888 | 1.1s | 1.3s | - | - | - | - |
| top20→rerank5 | 0.903 | 0.944 | 0.937 | 0.926 | 2.2s | 2.4s | - | - | - | - |
| +CRAG | 0.952 | 0.927 | 0.960 | 0.933 | 6.7s | 16.2s | 0.984 | **0.839** | 0.984 | 0.984 |

**四大结论**:
1. **独立judge撕开自评水分**: 真实答案正确率 **0.839**(对照参考答案, n=62) — 自评Faith/Cite 1.000时代结束; 事实支撑/引用经独立佐证0.984(质量底子真实, 但16%回答未完全命中参考答案)。
2. **hybrid卖点证伪**: 整体口径 dense 0.839 > hybrid 0.823(R@1); **关键词8问子集 dense 满分1.000, hybrid R@1反降0.875** — BM25理论主场也输。本语料(短文档, 精确token即主题词)下dense足够, BM25路(0.35权重)只引入噪声。→ 待办: RRF权重重调实验, 或README叙事如实改写。
3. **不可答负样本抓到幻觉路径**: 诚实降级率两轮合并 **17/20(0.85)**; `kb_grounded`编造引用3例(特斯拉Model Y/pgvector对比等被干扰项误导grade) — 无负样本时该路径永远测不出。→ 待办: grade拒答校准。
4. **纠错循环首次真实触发且奏效**: 1/62(重试问最终R@5=1.000且grounded ✓, 长尾归因表可查) — 第五轮改造的enough_evidence分流在真实失败上验证。

**工程教训(本轮排障全部为环境问题, 处置均已固化)**:

| 问题 | 处置 |
|---|---|
| Qdrant代理502打在finally掩盖根因 | 清理改告警不抛 |
| httpx读注册表系统代理(127.0.0.1:7890抖动) | 评测带`NO_PROXY=*`直连(三服务直连验证OK) |
| 机器待机静默杀长跑 | `SetThreadExecutionState`防待机(run_w2_eval内建) |
| DNS故障→断网(getaddrinfo failed/WinError 10051) | 后台探测循环等恢复 |
| **后台长跑~21分钟被静默杀死(两次, 日志冻结无Traceback, 非上述任何原因)** | **分块+断点续跑架构**: eval_crag逐问记录checkpoint jsonl, `--resumable`/`--max-q`/`--merge-from`(漏斗CSV确定性复用), 62问分6块全部完成 |
| judge选型27.9s/次(Qwen3.5-9B) | GLM-5.3-Flash 6-9s/次 |

**基线重立**: `baseline.md` 重建为golden v3口径(参与trial: `20261126_202308`; v2历史trial按口径过滤排除, aggregate_baseline `--v2`可切回)。`pytest` 47 passed(+6例golden v3完整性)。

**遗留待办(按优先级)**: ① RRF权重重调(0.65/0.35→扫参)或hybrid叙事改写; ② grade拒答校准(幻觉引用3/20); ③ 真实入库链路评测(分块/父块/截断全链路); ④ ReAct工具150字符截断上调; ⑤ P3方向(多租户/记忆A/B)待决策。

### CRAG 真纠错改造 + P2 减法(2026-11-26 第五轮)

**① CRAG 循环改真纠错**(`src/agents/graph.py` + `src/prompts/crag.py`, 修复评审发现"循环不纠错"):
- rewrite 携带失败上下文: verifier `issue` + 已试证据摘要 + 对话历史(旧实现只看原问题, 温度 0 下重复同一改写);
- verify 新增 `enough_evidence` 判定并按因分流: **证据缺口**→rewrite 改写重检索; **证据够但答案没写好**→直达 generate 重生成且 prompt 携带 verifier 反馈(温度 0 下无反馈重试只会逐字复现同一答案);
- generate/verify 证据同源 `_format_evidence`(旧 verify 缺父块上下文, 两侧证据集不一致);
- `chat_json` 解析失败返回的 error dict 不再被当"不 grounded"误触两轮完整重跑。

**② checkpointer 移除**(P2 减法): 多轮状态每次从 MySQL 重建, checkpoint 唯一用途是本次读回最终 state——`ainvoke`/`astream` 直接返回。连带消除: MemorySaver 驻留泄漏(实测≈13.7KB/会话全量累积)、`LANGGRAPH_STRICT_MSGPACK` 环境变量注入(`src/__init__.py`)、`RetrievedDocument` msgpack 白名单遗留检查项。6 处 `aget_state` 消费方(query 两路/ablation/2 脚本/单测)改为 `ainvoke` 直返或 updates 流累积。

**③ 死代码与依赖裁剪**: 删 `src/agents/tools.py`(445 行, import 即失败并拖坏 4 个脚本→已改指向活模块)、memory 设计稿三件套 conversation/user_profile/retriever(~1.4k 行自述"未接入")、`storage/pg.py`+schema(DESIGN.md 遗留, PostgreSQL 从未启用)、`src/core/llm.py`(被同名包遮蔽的 shim)、`DocumentCache`(185 行无调用方)。依赖移除 litellm/celery/docling/pymysql/python-multipart/sentence-transformers/FlagEmbedding/ragas(运行时零 import), 补显式声明 python-dotenv; **干净安装不再拉 torch**(import src.agents.graph 冷启动 12.0s→2.7s 实测)。`.env.example` 补 `JWT_SECRET`(部署必设)/`CACHE_SEMANTIC_ENABLED`, 删 Celery 段。

**④ 回归验证(golden v2 同口径, trial=`20261126_013003`**, [docs/trials/20261126_013003_w2_ablation.md](trials/20261126_013003_w2_ablation.md)**)**:

| 指标 | baseline.md(3轮均值) | 本次 | 判定 |
|---|---|---|---|
| R@1 | 0.938±0.010 | 0.944 | 噪声带内(=历史最佳单轮) |
| R@5 / MRR@5 / nDCG@5 | 0.935 / 0.963 / 0.937 | 0.935 / 0.963 / 0.937 | 逐位持平 |
| Faith / Cite | 1.000 | 1.000 | 持平 |
| p50 | 9474±161ms | **8260ms(-12%)** | 改善, 超出噪声带 |
| p95 | 16394±1107ms | **14563ms(-11%)** | 改善, 超出噪声带 |
| 重试 | 0/54 | 0/54 | 持平 |

读数: 纠错分流逻辑为**潜伏能力**(golden v2 全可答, verifier 全过 → 0 重试不触发), 质量指标无回退; p50/p95 改善 11-12% 主要来自 checkpointer 移除(每 superstep 不再做 checkpoint 序列化/白名单合并), 单次运行结论, 多轮均值追踪交由后续 trial。纠错路径的行为正确性由离线假对象单测锁定(`tests/unit/test_correctness_fixes.py`)。

**单元回归**: `pytest tests -q` → 41 passed; ruff E9/F passed; `import apps.api.main` / `src.memory` / `src.cache` / `src.evaluation.ablation` OK; 死引用 grep 干净(仅存"已删"注释)。main CI: run 45665327202 success。

**遗留(P3, 待定方向)**: 多租户贯通 vs 诚实单租户(二选一); 记忆系统开关 A/B 定去留; 入库 BackgroundTasks 持久化队列(用 `indexing_jobs` 表做 SKIP LOCKED 认领); ReAct 工具结果 150 字符截断上调; 评测效度补齐(参考答案/不可答集/独立 judge/关键词型查询)。

### 设计评审修复: 六项正确性缺陷(2026-11-26 第四轮)

评审方法: 通读全部核心模块 + 只读复现脚本离线坐实(LLM/Qdrant/OSS 全假对象替换, 不动真实服务), 每项缺陷转化为 `tests/unit/test_correctness_fixes.py` 回归断言。

| # | 缺陷(复现证据) | 修复 | 验证 |
|---|---|---|---|
| 1 | 入口闲聊正则子串匹配: "你好，我们公司的报销流程是什么？"被误判闲聊**跳过知识库直答**;"Redis怎么用"被"怎么用"误判 | 正则下沉 `src/agents/routing.py`, 闲聊/能力问询改**整句 fullmatch**; 夹带正事的问候走正常路由 | 复现脚本 B 组 10 例 + 单测 26 例 |
| 2 | 安全正则单词命中: "预防**网络攻击**的安全规范""对**炸弹**威胁的处置流程"被误拒 | "请求动词+危害对象"**双条件**共现才拒(动词表不含"如何/怎么"); 疑问式/模糊越界交模型护栏(TOOL_AGENT_PROMPT 规则 5 / GENERATOR_PROMPT 规则 4 新增) | 复现脚本 B 组 + 单测 |
| 3 | kb 模式证据全被判无关后转 tool_agent 兜底, **citations 仍返回刚被判无关的文档**(引用与答案脱节, 违背引用溯源定位) | `_grade_node` 两处降级 Command 显式 `documents=[]` | 假对象图复现: citations=[] + source=tool |
| 4 | 父块只存组内**第一段**: `split_parent_child` 各 child 的 parent_content 是累加快照, OSS 落盘取首个非空 child → 组内第 2+ 个 child 命中所见"父块"只有第 1 段, 父块展开名存实亡(旧 `test_parent_expansion.py` 只断言非空故一直 PASS) | 每组**最后一个 child 持有完整组文本**, 回填全组; generate 注入侧加 `EVIDENCE_PARENT_MAX_CHARS=1200` 截断防 prompt 膨胀 | 5 段语料复现: 全部 child 可见 1-5 段 + 单测含组切换边界 |
| 5 | 语义缓存词袋重叠**词序/否定词盲**: "Redis支持持久化吗"vs"不支持"相似度 0.95 命中(停用词表丢弃"不"); "项目A负责人"vs"项目B"命中 | **默认关闭**(`QueryCache.enable_semantic=False` + `CACHE_SEMANTIC_ENABLED` env 可回开); 精确缓存不受影响; `scripts/test_semantic_cache.py` 头部标注 env 要求; 升级向量相似度列为 roadmap | 单测存档反义误命中证据 |
| 6 | `OssStore` async def 内直调同步 oss2: 5 路并发 GET **实际串行 1.0s, 事件循环停摆 1002ms**(to_thread 对照组 0.20s/29ms) | get/put/delete/delete_by_prefix 全部包 `asyncio.to_thread`; sign_url 本地签名保持同步; 入库侧 PyMuPDF 解析同步抽 `_extract_pdf_texts` + to_thread | 单测: 5×0.2s 并发 wall<0.45s 且循环停顿<150ms |

附加修复:
- **MemorySaver 驻留泄漏**: 实测 ≈13.7KB/会话全量驻留(300 会话 4.1MB, 无任何清理)。多轮状态每次从 MySQL 重建, checkpoint 仅本次读回用 → query.py 两路响应后 `_release_thread_state` 摘除线程条目(当前 langgraph 无 `adelete_thread`, 用私有 storage.pop, 升级时复查)。
- **依赖声明补齐**(干净环境 `pip install -e .` 此前起不来): `passlib`+`bcrypt`(auth.py 哈希)、`python-jose[cryptography]`(JWT)、`PyMuPDF`(import fitz, Docling 弃用后的实际解析器)、`sse-starlette`(EventSourceResponse)。CI 闭包同步追加 langgraph/langchain/oss2(新单测所需)。

回归验证: `python -m pytest tests -q` → **41 passed**(原 5 + 新 36); ruff E9/F passed; `import apps.api.main` ok; 复现脚本复跑六项全部翻转。golden v2 口径影响评估: ①②③⑤⑥ 不触碰检索打分链路, ④ 仅影响真实入库链路(golden v2 评测走直连 chunk, 不经分块), 基线数字预期不变, 未重跑。

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
