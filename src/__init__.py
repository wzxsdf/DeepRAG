# ponytail: src包标记。历史曾在此注入 LANGGRAPH_STRICT_MSGPACK 环境变量(配合 MemorySaver
# with_allowlist 序列化白名单), checkpointer移除(评审P2减法)后无存在意义, 已删;
# 保留空init避免namespace package在打包/IDE解析上的差异。
