# RAG Harness 实施排期

> 对应 [RAG_HARNESS_DESIGN.md](RAG_HARNESS_DESIGN.md) §9 路线图,拆解到任务级。
> 估算基准:单人开发,任务级估时已含编码 + 自测 + 文档;不含评审与返工缓冲(另列)。
> 文档日期: 2026-11-24 · 版本 v1.0

---

## 1. 排期原则

1. **正确性 > 结构 > 能力**:P0 修复先行,任何新能力不建立在已知 bug 上。
2. **评测先于优化**:所有 P4 深度能力必须在 Eval Harness(P2/P3)就绪后才开工 —— 没有消融框架的优化是盲改。
3. **内容与代码双轨并行**:golden 数据集写题是内容工作,不阻塞代码轨道,穿插进行。
4. **渐进迁移不重写**:P1 数据契约以新模块并行落地,旧调用方逐个切换,全程保持旧评测绿(否则中途不可交付)。
5. **每项能力"不涨数字不留"**:P4 各项独立立项,消融不达标即砍,时间池不追加。

---

## 2. 总量与里程碑

| 里程碑 | 内容 | 工作量 | 累计(人日) |
|---|---|---|---|
| **M1 正确性基线** | P0 三项修复 + 漏斗升级出数字 | 3.5 d | 3.5 |
| **M2 契约+金标准** | P1 数据契约迁移 + golden v2(≥50 条) | 12 d | 15.5 |
| **M3 Eval Harness 完整** | 指标注册表 + judge 纪律 + scenario runner + CI 门禁 | 10 d | 25.5 |
| **M4 深度能力(选 2 项)** | query 变换 / doc-type 分块 / CRAG NLI 三选二 | 6 d | 31.5 |
| **缓冲 15%** | 返工 / 环境问题 / judge 调试 | 4.5 d | **36** |

- **全职(5 人日/周)**:≈ 7 周
- **业余(2.5 人日/周)**:≈ 14 周;若压缩,建议砍 M4 到 1 项 → ≈ 12 周
- P5(记忆升级)不进本期排期,列 backlog(§6)。

里程碑判据(Definition of Done):

| 里程碑 | 通过标准 |
|---|---|
| M1 | 三个修复各有回归测试;`run_w2_eval.py` 新增漏斗对比行(recall@5: top_k=20+rerank vs top_k=5+rerank) |
| M2 | 组件间零裸 dict 传参;golden v2 四类分布(factual/multi-hop/conditional/out-of-scope)且每类 ≥10 条 |
| M3 | `python -m harness.run --scenario base` 一条命令产出完整报告(trial 目录 + bootstrap CI + markdown);CI 对 golden 检索指标设门禁 |
| M4 | 每项能力有消融对照表,明确的"留/砍"结论写入 docs |

---

## 3. 甘特总览(全职口径,周为单位)

```
                W1    W2    W3    W4    W5    W6    W7
P0 正确性      ████
P1 数据契约          ████████
golden 内容轨  ·····█████·····(穿插, 不占满)
P2 Eval引擎               ██████████
P3 消融+门禁                        ██████
P4 能力A/B                                  ██████
缓冲                                                ████
M里程碑        ▲M1         ▲M2               ▲M3   ▲M4
```

依赖关系:`P2 指标注册表` 只依赖 P0(不依赖 P1,可与其部分并行);`P3` 依赖 P2;`P4` 依赖 P3;golden 内容轨全程独立。

---

## 4. 任务分解明细

### P0 正确性基线(3.5 人日,W1)

| # | 任务 | 估时 | 交付判据 |
|---|---|---|---|
| 0.1 | 缓存版本失效:query cache key 携带 kb 版本号;入库/删除/切版本时按 kb 失效 | 1 d | 文档重建后旧答案 100% 失效(集成测试);命中缓存也写 trace |
| 0.2 | RetrievalFilter 补 acl_tags + active_version 过滤(Qdrant must filter) | 0.5 d | 非授权标签 chunk 召回率 = 0;旧版本 chunk 召回率 = 0(测试) |
| 0.3 | 重排漏斗:recall top_k 5→20,parent 去重,rerank→top5 | 0.5 d | 漏斗延迟增量 <300ms(p95 口径) |
| 0.4 | 消融重跑:新增漏斗对比配置行,记录基线数字进 docs | 1 d | `w2_ablation` 报告新增 funnel 行 + 结论一段 |
| 0.5 | 三项修复各配回归测试 | 0.5 d | tests/ 通过,后续阶段不回退 |

### P1 数据契约(7 人日,W2-W3)

| # | 任务 | 估时 | 交付判据 |
|---|---|---|---|
| 1.1 | 新建 `src/contracts/`:Chunk / QueryBundle / Evidence / RetrievalResult / CaseTrace 五 dataclass + 字段文档 | 2 d | 契约单测;与现有 RetrievedDocument 差异清单 |
| 1.2 | 检索链迁移:HybridRetriever/Reranker 输入输出切到契约 | 2 d | 旧 ablation 数字复现(±1%);trace 中 Evidence 分阶段得分可见 |
| 1.3 | Agent 图迁移:graph.py 内部步骤改调 L3 组件(消除内联检索逻辑) | 1.5 d | graph 与 pipeline 共享同一 retriever 实例;agent 消融可用 |
| 1.4 | Registry + YAML 实例化(import 白名单 + init 签名校验) | 1.5 d | 恶意/未知参数 YAML 拒载(测试);现有 pipeline 可由 YAML 重建 |

### golden v2 内容轨(5 人日,穿插 W2-W4,不阻塞)

| # | 任务 | 估时 | 交付判据 |
|---|---|---|---|
| G.1 | 手写核心 20 条(factual 8 / multi-hop 6 / conditional 4 / out-of-scope 2),含 gold_claims | 2 d | 每条带 claims 级标注 |
| G.2 | KG 式自动出题脚本(文档→切分→实体抽取→组合出题)+ 人工审核队列 | 1.5 d | 自动生成 60+ 候选,审核后 ≥30 条入库 |
| G.3 | golden v2 定版:四类各 ≥10,共 ≥50,版本化 yaml | 1.5 d | 分布报告;每条可追溯到源文档 |

### P2 Eval 引擎(10 人日,W4-W5)

| # | 任务 | 估时 | 交付判据 |
|---|---|---|---|
| 2.1 | BaseMetric 自注册(`__subclasses__`)+ 经典指标(recall@k/MRR/nDCG)迁移 | 1.5 d | 新指标=1 个类接入,零改动注册表 |
| 2.2 | claim 级指标:claim recall + faithfulness(claims→entailment 比值) | 2 d | Ragas 范式实现,输出逐 case claims 明细 |
| 2.3 | judge 基础设施:独立 judge endpoint(与生成不同家族)、invalid_result 处理、temperature=0 | 1.5 d | judge 配置与业务配置隔离;解析失败计入报表不静默 |
| 2.4 | answer relevancy(反向问题+余弦)+ out-of-scope 拒答率指标 | 1 d | 两指标有单测 |
| 2.5 | position calibration(pairwise swap 求均值)+ verbosity 分桶检查 | 1 d | calibration 开关可配置 |
| 2.6 | bootstrap CI(95%,重采样)+ 报告生成器(trial 目录 + summary.csv + markdown) | 1.5 d | 报告含 CI 区间;config fingerprint 反查可用 |
| 2.7 | `harness.run` 统一入口(--scenario/--metrics/--judge) | 1.5 d | 一条命令出全量报告;检索缓存复用生效 |

### P3 消融 + 门禁(6 人日,W6)

| # | 任务 | 估时 | 交付判据 |
|---|---|---|---|
| 3.1 | Scenario Runner:matrix 笛卡尔展开、节点局部指标预筛、逐 case metrics 落盘 | 2.5 d | `rerank_funnel.yaml` 一键跑 4×2 矩阵;产物可复现 |
| 3.2 | 检索 GT 重映射(换分块策略后 gold_doc_ids 自动重映射) | 1 d | 两种分块策略下指标可比(测试) |
| 3.3 | CI 集成:GitHub Actions,PR 跑检索指标门禁,nightly 跑全量生成指标 | 1.5 d | 故意改坏 rerank 的 PR 被门禁拦截(演练一次) |
| 3.4 | eval_gate.yaml(绝对下限 + 相对回退限 + CI 重叠判定) | 1 d | 门禁规则文档化;线上失败 trace → golden 候选通道打通 |

### P4 深度能力(三选二,6 人日,W7,每项独立立项)

| 选项 | 任务 | 估时 | 前置 | 预期消融 |
|---|---|---|---|---|
| A | Query 变换家族:统一协议 + rewrite / HyDE(只改 embedding_strs) / sub-question(并行融合) | 3 d | P3 | {none, rewrite, hyde, subq} × {top_k 5, 20} |
| B | doc-type 模板分块:PyMuPDF dict 模式取字体层级→heading_path;表格转 HTML 单块;QA 模板(问题匹配/答案生成分离) | 4 d | P3 + G.T重映射 | vs fixed 切分全指标对比 |
| C | CRAG NLI 评估器:0.77B NLI 双阈值三分类 + Incorrect→web 兜底 + decompose-recompose | 3 d | P3 | grader 准确率(vs LLM grader)+ 延迟对比 |

选择建议:**优先 A + C**(B 收益最大但工作量超出 6 人日池,若前序有结余再上)。每个选项结项时把"留/砍"结论 + 消融表写入 `docs/`。

---

## 5. 风险与应对

| 风险 | 概率 | 影响 | 应对 |
|---|---|---|---|
| P1 重构牵连过广,旧功能回归 | 中 | 高 | 契约模块并行落地、逐调用方切换;每个子任务收尾跑旧 ablation(±1% 内) |
| golden 写题内容工作量失控 | 高 | 中 | KG 生成 + 审核提效;每周固定产出 10-15 条;宁缺毋滥——bad case 入集比不入危害大 |
| judge 调用成本/稳定性 | 中 | 中 | PR 只跑检索指标(零 judge);生成指标 nightly 批量;faithfulness 可切 HHEM 开源分类器 |
| NLI 小模型本地部署(选项 C) | 中 | 中 | 优先 SiliconFlow 托管端点;本地 vLLM 仅作降级备选 |
| 指标与人类判断不符(评测不可信) | 低 | 高 | 预留 30 条人工偏好标注做 mini meta-eval;相关系数 <0.5 时先修指标再谈优化 |
| Windows 环境坑(复现 Docling 式问题) | 中 | 低 | 新解析类统一走 Linux 容器内验证;本地开发用 PyMuPDF 纯路径 |

---

## 6. 范围外(本期不做)

- P5 记忆升级(Mem0 写管道 / bi-temporal / 个性化 rerank)→ backlog,待 M4 结束后另立排期
- GraphRAG / LightRAG 图检索、多模态、MCP server、K8s 部署、前端可视化增强
- 任何未进本表的"顺手优化"——一律进 backlog,不占当前时间池

---

## 7. 节奏建议

- **每周五**:跑一次全量 eval 存档(trial 目录按日期),形成基线曲线 —— 简历叙事"指标随周演进"的直接素材
- **每个里程碑结项**:更新 docs 里的验收报告(沿用 W1/W2/W3_VERIFICATION.md 的格式),数字先行
- **golden 内容轨**:每周固定 1-2 小时写题 + 审核自动生成候选, W4 结束前达标 50 条
