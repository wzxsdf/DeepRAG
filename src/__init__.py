# ponytail: src包初始化 - 必须在首个langgraph导入前执行(任何 import src.* 都先经过此处)
import os

# LangGraph checkpoint msgpack 反序列化默认走宽容模式(allow+warn)。
# graph.py 的 with_allowlist 在宽容模式下是静默 no-op(serde.with_msgpack_allowlist 对 base=True 直接返回自身),
# 只有 strict 模式下白名单才真正生效。此处设默认 strict(已实测 RetrievedDocument 白名单下 CRAG 全链路跑通);
# 用户显式设置 LANGGRAPH_STRICT_MSGPACK=false 可回退宽容模式。
os.environ.setdefault("LANGGRAPH_STRICT_MSGPACK", "true")
