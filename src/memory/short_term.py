# 短期记忆治理组件 — P0 核心
#
# 职责: 把任意长度的对话历史治理成「摘要 + 近期原文」的受控窗口, 防 context 爆满。
# 解耦: 纯组件, 输入 history(list[dict]) → 输出 ManagedHistory, 不依赖 graph/agents/api。
# 即插即用: 行为由 MemoryConfig 控制 — 关摘要(仅窗口) / 关治理(直通) / 调窗口大小。
# 治理权单一: 调用方(graph/query)传入的 history 已治理, 消费方不再自行裁剪。
#
# 对应设计文档 docs/MEMORY_LAYER_DESIGN.md §6.2(P0主方案: 滑动窗口 + 摘要)。

from dataclasses import dataclass, field
from typing import Callable, Awaitable, Optional

from src.core.logging import log


# ponytail: token 估算 — 4字符≈1token(中英混合经验值, 避免引tiktoken依赖)
CHARS_PER_TOKEN = 4

# 摘要提示词: 聚焦"下次任务有用"的信息(关键决策/偏好/确认), 对应设计文档"存什么"过滤标准
_SUMMARIZE_PROMPT = """把以下对话历史压缩成一段简洁摘要, 供后续对话参考。
只保留对后续任务有价值的信息: 关键决策、用户明确表达的偏好、已确认的事实/方案。
丢弃: 闲聊、中间推理、已废弃的方案。

对话历史:
{text}

直接输出摘要(不超过200字), 不要解释:"""


@dataclass
class MemoryConfig:
    """短期记忆治理配置。即插即用的"旋钮"。"""
    enabled: bool = True               # 总开关; False=组件直通(不裁剪不摘要, 兼容旧行为)
    max_context_tokens: int = 2000     # 历史占用 token 预算; 超过才触发摘要+窗口
    keep_recent_turns: int = 4         # 触发压缩时, 近期保留的原文轮数
    enable_summary: bool = True        # 是否启用 LLM 摘要; False=超预算时仅硬截断(纯窗口)
    summary_max_chars: int = 200       # 摘要最长字符数


@dataclass
class ManagedHistory:
    """治理结果。承载摘要 + 近期原文, 可还原为兼容旧格式的 history list。"""
    summary: Optional[str]          # 旧历史摘要; None 表示未压缩(直通)
    recent_turns: list[dict]        # 近期原文轮 [{question, answer}]
    original_count: int             # 治理前的总轮数(可观测)
    estimated_tokens: int           # 治理后估算 token(可观测)
    compressed: bool                # 是否触发了压缩

    def to_history_list(self) -> list[dict]:
        """还原为 graph 消费的 history 格式。摘要把为首条虚拟轮次(标注前缀)。"""
        result: list[dict] = []
        if self.summary:
            result.append({"question": "(历史摘要)", "answer": self.summary})
        result.extend(self.recent_turns)
        return result


# 摘要器签名: 输入待摘要文本 → 输出摘要文本。可注入(测试/换模型)。
Summarizer = Callable[[str], Awaitable[str]]


async def default_summarizer(text: str) -> str:
    """默认摘要器: 走 core.llm.chat(fast purpose, 与 rewrite/兜底同源)。

    ponytail: 延迟 import 避免循环; 失败由调用方降级。
    """
    from src.core.llm import chat
    return await chat([{"role": "user", "content": _SUMMARIZE_PROMPT.format(text=text)}])


class ShortTermMemoryManager:
    """短期记忆治理器 — 即插即用组件。

    用法:
        manager = ShortTermMemoryManager(config)
        managed = await manager.manage(history)   # history: [{question, answer}, ...]
        graph_history = managed.to_history_list()

    策略(对应设计文档 §6):
        1. 未超 max_context_tokens → 直通(无摘要)
        2. 超预算 + enable_summary → 旧历史 LLM 摘要 + 近期 keep_recent_turns 原文
        3. 超预算 + 禁摘要        → 纯窗口(硬截断到 keep_recent_turns)
        4. 摘要失败               → 降级纯窗口(不崩, 不阻塞回答)
    """

    def __init__(
        self,
        config: Optional[MemoryConfig] = None,
        summarizer: Optional[Summarizer] = None,
    ):
        self.config = config or MemoryConfig()
        self._summarizer = summarizer or default_summarizer

    async def manage(self, history: Optional[list[dict]]) -> ManagedHistory:
        """治理对话历史。输入 None/空 → 返回空结果(不报错)。"""
        history = history or []
        cfg = self.config

        # ① 总开关关闭: 直通(兼容旧行为, 消费方需自行兜底)
        if not cfg.enabled or not history:
            return ManagedHistory(
                summary=None, recent_turns=list(history),
                original_count=len(history),
                estimated_tokens=self._est_tokens(history),
                compressed=False,
            )

        total_tokens = self._est_tokens(history)

        # ② 未超预算: 直通(近期=全部)
        if total_tokens <= cfg.max_context_tokens:
            return ManagedHistory(
                summary=None, recent_turns=list(history),
                original_count=len(history),
                estimated_tokens=total_tokens, compressed=False,
            )

        # ③ 超预算: 切分近期原文 + 旧历史
        # ponytail: keep_recent_turns 不能超过历史长度; 至少留1轮给摘要兜底
        keep = min(cfg.keep_recent_turns, len(history) - 1) if len(history) > 1 else len(history)
        recent = history[-keep:] if keep > 0 else []
        to_summarize = history[:-keep] if keep > 0 else list(history)

        summary = None
        if cfg.enable_summary and to_summarize:
            summary = await self._safe_summarize(to_summarize)

        managed = ManagedHistory(
            summary=summary,
            recent_turns=recent,
            original_count=len(history),
            estimated_tokens=self._est_tokens(recent) + self._est_tokens([{"q": summary}] if summary else []),
            compressed=True,
        )
        log.info(
            "short_term_memory_managed",
            original=managed.original_count, recent=len(recent),
            summarized=len(to_summarize), has_summary=bool(summary),
            est_tokens=managed.estimated_tokens,
        )
        return managed

    async def _safe_summarize(self, turns: list[dict]) -> Optional[str]:
        """安全摘要: 失败降级返回 None(调用方退化为纯窗口)。"""
        try:
            text = "\n".join(
                f"用户: {t.get('question', '')}\n助手: {t.get('answer', '')}" for t in turns
            )
            summary = await self._summarizer(text)
            summary = (summary or "").strip()
            # ponytail: 截断超长摘要, 保 token 受控
            return summary[:self.config.summary_max_chars] if summary else None
        except Exception as e:
            log.warning("short_term_summary_failed", error=type(e).__name__, msg=str(e))
            return None

    @staticmethod
    def _est_tokens(history: list[dict]) -> int:
        """估算 history 的 token 数(字符数/4)。"""
        chars = sum(len(h.get("question", "")) + len(h.get("answer", "")) for h in history)
        return chars // CHARS_PER_TOKEN


# ponytail: 模块级单例(非 web 场景/脚本用); web 场景经 lifespan 注入。
_stm_manager: Optional[ShortTermMemoryManager] = None


def get_short_term_manager(
    config: Optional[MemoryConfig] = None,
    summarizer: Optional[Summarizer] = None,
) -> ShortTermMemoryManager:
    """获取短期记忆治理器单例(懒加载)。lifespan 优先注入实例。"""
    global _stm_manager
    if _stm_manager is None or config is not None:
        _stm_manager = ShortTermMemoryManager(config=config, summarizer=summarizer)
    return _stm_manager


def set_short_term_manager(manager: ShortTermMemoryManager) -> None:
    """lifespan 启动时注入实例(覆盖单例)。"""
    global _stm_manager
    _stm_manager = manager


if __name__ == "__main__":
    # ponytail: 自检 — 窗口/摘要/降级三路径可运行验证
    import asyncio

    async def _noop_summarizer(text: str) -> str:
        return "【测试摘要】用户偏好Python简洁风格"

    async def main():
        # 构造10轮假历史
        hist = [{"question": f"问题{i}", "answer": "答" * 100} for i in range(10)]

        # 1. 直通(预算大)
        m1 = ShortTermMemoryManager(MemoryConfig(max_context_tokens=99999), _noop_summarizer)
        r1 = await m1.manage(hist)
        assert not r1.compressed and len(r1.recent_turns) == 10, "直通失败"
        print(f"[OK] 直通: {len(r1.recent_turns)}轮, compressed={r1.compressed}")

        # 2. 摘要+窗口(预算小)
        m2 = ShortTermMemoryManager(MemoryConfig(max_context_tokens=200, keep_recent_turns=3), _noop_summarizer)
        r2 = await m2.manage(hist)
        assert r2.compressed and r2.summary and len(r2.recent_turns) == 3, "摘要窗口失败"
        out = r2.to_history_list()
        assert out[0]["question"] == "(历史摘要)", "摘要未置首"
        print(f"[OK] 摘要+窗口: summary={'有' if r2.summary else '无'}, recent={len(r2.recent_turns)}, 输出{len(out)}条")

        # 3. 禁摘要(纯窗口)
        m3 = ShortTermMemoryManager(MemoryConfig(max_context_tokens=200, keep_recent_turns=3, enable_summary=False), _noop_summarizer)
        r3 = await m3.manage(hist)
        assert r3.compressed and r3.summary is None and len(r3.recent_turns) == 3, "纯窗口失败"
        print(f"[OK] 纯窗口: summary=None, recent={len(r3.recent_turns)}")

        # 4. 摘要失败降级
        async def _bad_summarizer(text: str) -> str:
            raise RuntimeError("模拟失败")
        m4 = ShortTermMemoryManager(MemoryConfig(max_context_tokens=200, keep_recent_turns=3), _bad_summarizer)
        r4 = await m4.manage(hist)
        assert r4.compressed and r4.summary is None, "降级失败"
        print(f"[OK] 摘要降级: 失败后 summary=None, recent={len(r4.recent_turns)}")

        # 5. 空历史
        r5 = await m1.manage([])
        assert r5.recent_turns == [] and not r5.compressed, "空历史失败"
        print("[OK] 空历史直通")

        print("\nshort_term self-check passed")

    asyncio.run(main())
