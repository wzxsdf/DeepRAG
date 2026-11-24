# P0 正确性基线验收

> 日期: 2026-11-24
> 范围: 查询缓存作用域、活动版本/召回过滤、检索候选池与 parent 去重

## 已完成

- 查询缓存增加租户 generation。知识库入库成功和文档删除前提升 generation，旧精确缓存和语义缓存候选均不可继续命中。
- 语义缓存命中增加 generation、tenant、kb_ids、mode 全量 scope 校验，避免跨租户、跨知识库和跨模式污染。
- Qdrant dense/sparse 两路共用 tenant、kb、ACL、active version 过滤条件。ACL 标签按每个标签分别加入 must 条件。
- `RetrievalFilter` 增加多版本 `active_version_ids`，支持多知识库查询。数据库没有活动指针时回退到已索引最新版本。数据库明确返回无可用版本时使用不可能匹配的版本条件，避免空列表放宽为全量召回。
- 入库成功路径按“索引写入 → 版本 INDEXED → active pointer 切换 → 缓存失效 → SUCCESS”执行。active pointer 切换失败会进入 FAILED。
- 文档和知识库删除复用租户级缓存失效钩子。
- 检索后精排候选池统一扩大到 20，Cross-Encoder 前按 parent_id 去重，失败回退也保持去重。
- KB 模式、ReAct 工具、Plan Agent 和旧 Agent 工具路径均使用统一候选池常量。

## 验证结果

| 检查 | 结果 |
|---|---|
| `python -m pytest tests/unit -q` | 5 passed |
| 修改模块 `py_compile` | passed |
| P0 运行时 lint（E9/F） | passed |
| `git diff --check` | passed |
| `python -m pytest -q` | 收集阶段失败，未进入断言 |

完整 pytest 的失败来自项目现有脚本在收集阶段创建 aiomysql 连接，事件循环关闭后触发资源析构异常，随后 pytest capture 文件已关闭。该问题没有出现在新增 P0 单元测试中。当前没有运行依赖外部 MySQL、Redis、Qdrant、OSS 或模型服务的集成验证，因此真实检索延迟和召回增益仍需在依赖服务可用时运行 `scripts/test_hybrid_retrieval.py` 与 `scripts/run_w2_eval.py` 记录。
