# ponytail: 集中配置(pydantic-settings) — 消除散落os.getenv, 一处定义/类型校验/环境隔离
# 阶段1聚焦 MySQL/Qdrant/OSS/API + LLM; Redis/Celery/Langfuse 后续阶段收口
# 字段名对应ENV key(case_sensitive=False, mysql_host ↔ MYSQL_HOST); 新purpose路由字段阶段2启用

from dataclasses import dataclass

from pydantic_settings import BaseSettings, SettingsConfigDict


# === 子配置 dataclass(给存储类构造注入, 阶段1.2) ===
@dataclass
class MySQLCfg:
    host: str = "localhost"
    port: int = 3306
    db: str = "deeprag"
    user: str = "deeprag"
    password: str = ""


@dataclass
class QdrantCfg:
    host: str = "http://localhost"
    port: int = 6333
    api_key: str = ""


@dataclass
class OSSCfg:
    endpoint: str = ""
    bucket: str = "deeprag-dev"
    access_key_id: str = ""
    access_key_secret: str = ""


@dataclass
class ProviderCfg:
    # ponytail: provider连接信息; 由 providers property 从扁平env派生, 加新provider=加字段+映射
    api_key: str = ""
    base_url: str = ""


class Settings(BaseSettings):
    """全局配置(从 .env 加载). 替代散落各处的 os.getenv。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # --- MySQL (兼容 MYSQL_DATABASE 与 MYSQL_DB 两种key) ---
    mysql_host: str = "localhost"
    mysql_port: int = 3306
    mysql_database: str = ""
    mysql_db: str = "deeprag"
    mysql_user: str = "deeprag"
    mysql_password: str = ""

    # --- Qdrant ---
    qdrant_host: str = "http://localhost"
    qdrant_port: int = 6333
    qdrant_api_key: str = ""

    # --- 阿里云 OSS ---
    oss_endpoint: str = ""
    oss_bucket: str = "deeprag-dev"
    oss_access_key_id: str = ""
    oss_access_key_secret: str = ""

    # --- API ---
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # --- LLM 扁平key(chat走litellm/juaiapi, embed/rerank走embedding/siliconflow; 存储类+ModelGateway共用) ---
    litellm_base_url: str = ""
    litellm_api_key: str = ""
    litellm_model: str = "gpt-4o-mini"
    embedding_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_api_key: str = ""
    embedding_model: str = "BAAI/bge-m3"
    embedding_dimensions: int = 1024
    tavily_api_key: str = ""

    # --- LLM purpose路由(阶段2 ModelGateway); 空→回退 openai:{litellm_model} 兼容现有单端点 ---
    chat_generate_model: str = ""   # CRAG生成(强); 配 "provider:model" 启用多模型路由
    chat_fast_model: str = ""       # grade/judge/rewrite(快省)
    chat_reason_model: str = ""     # plan规划(推理)
    # ponytail: 评测独立judge模型(评审P1②) — 必须与generate/fast异模型才有独立效度; 空=judge关闭(评测回退自评口径)
    chat_judge_model: str = ""

    # --- purpose级输出token封顶; 0=不封顶(旧行为) ---
    # ponytail: CRAG回答为短事实+引用, 长输出只在长尾出现, 封顶直接压p95(golden v2实测长尾generate单节点最长12.5s)
    chat_generate_max_tokens: int = 0
    chat_fast_max_tokens: int = 0

    # --- Langfuse(LLM trace, 阶段2接ModelGateway); 三key齐全即启用 ---
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # --- Redis(记忆模块+缓存系统, 阶段W4) ---
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""
    redis_db: int = 0  # ponytail: 记忆db0/查询缓存db1/文档缓存db2, 实例级单URL按用途分库

    # --- 查询缓存(评审修复20261126) ---
    # 语义缓存默认关闭: 词袋重叠方案词序/否定词盲("支持X吗"vs"不支持X"误命中实测0.95);
    # 精确缓存不受影响照常生效。确认升级为向量相似度后再开启。
    cache_semantic_enabled: bool = False

    # --- 检索拒答校准(20261126, P1②负样本实测) ---
    # kb模式grade前阈值短路: rerank分数低于此值→直接判无证据降级(不过grader LLM)。
    # 标定: golden 32问top1中位0.927 / 不可答10问中位0.012, 双峰分布0.03~0.8有空隙, 0.30取中点。
    # 0=关闭短路(旧行为)。
    retrieval_min_score: float = 0.30

    # --- hybrid稀疏路权重(20261126扫参后下调) ---
    # RRf扫参(golden v3 62问): sparse权重0.15与纯dense逐位持平; 0.35(旧默认)R@1 -1.6pt;
    # 0.5/0.7 -3.2pt — 单调恶化, 无任何权重赢dense。短文档语料精确token即主题词, dense足够。
    # 默认0.15: 生产≈dense(实证最优), 稀疏路保留应对真实长文档语料; 0=纯dense。
    retrieval_sparse_weight: float = 0.15

    # --- kb模式检索前query预改写(20261127, golden v3量化缺口) ---
    # 口语化问题(62问中12问)首轮检索无法以score>0.30召回证据→被拒答阈值误降级; 根因是口语表达
    # 与文档关键词化表述(术语/专名)向量不匹配。kb模式检索前先经fast模型改写为检索友好表达
    # (口语→关键词化+指代消解), 成本1次fast调用(~1s)。False=关闭(旧行为)。
    retrieval_query_rewrite: bool = True
    # 门控(P1延迟回收): 口语标记/多轮指代才改写, 关键词式(含ASCII精确token)直通跳过。
    # 离线验证golden v4: 跳过23/100且全部为关键词式问(golden上口语问已占多数, 生产kb查询
    # 关键词式占比更高→省得更多)。False=每问都改写(20261127治疗组口径)。
    retrieval_query_rewrite_gate: bool = True

    # --- LLM请求超时(20261127) ---
    # 单次LLM HTTP调用的硬超时(秒): openai SDK默认600s且chat_json有3套兜底方案×max_retries,
    # 端点黑洞(无RST的挂起连接)时单问可挂~30min(20261127实测评测卡死13min+)。120s兜底足够
    # 覆盖正常调用(flash模型p99<30s), 超时走异常路径(自愈重试/评测可疑计数)。
    chat_request_timeout: int = 120

    # --- rerank模型(20261127, P0精排优化A/B) ---
    # SiliconFlow同端点可换: BAAI/bge-reranker-v2-m3(现役) / Qwen/Qwen3-Reranker-0.6B|4B|8B /
    # Pro/BAAI/bge-reranker-v2-m3(独享档, 漂移稳定性待查)。分数分布随模型变 — 换模型必须
    # 重标定 retrieval_min_score 与 rerank_selfheal_min(标定脚本: scripts/calibrate_rerank.py)。
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    # 自愈重试触发线(原硬编码0.30, 与bge-v2-m3分数分布标定; 换模型后随retrieval_min_score一起重标)
    rerank_selfheal_min: float = 0.30
    # 精排后端(20261127 P0): api=SiliconFlow /rerank(默认, 零依赖) | local_onnx=本地int8量化
    # (确定性无跨窗漂移; 需 optional-deps[local-rerank] + scripts/export_reranker_onnx.py 导出权重)
    rerank_backend: str = "api"

    # --- 短期记忆治理(P0, 即插即用) ---
    memory_enabled: bool = True              # 总开关; False=组件直通(不裁剪不摘要, 兼容旧行为)
    memory_max_context_tokens: int = 2000    # 历史占用token预算; 超过才触发摘要+窗口
    memory_keep_recent_turns: int = 4        # 触发压缩时, 近期保留的原文轮数
    memory_enable_summary: bool = True       # 是否启用LLM摘要; False=超预算时仅硬截断(纯窗口)

    # --- 长期记忆(P1, 即插即用) ---
    # 20261126封存决定: 长期记忆链路(P1/P2/P3)代码保留但默认关断 — 从未做过质量A/B(注入的记忆
    # 可能增益也可能污染, 未验证答案也会沉淀), 且与DESIGN.md"长期记忆全砍"章程冲突; 检索/Agent优化
    # 优先, 记忆量化验证与重开列入后续agent/记忆优化阶段。置true可整体重开。
    memory_long_term_enabled: bool = False   # 长期记忆读闭环开关; False=不建agent_memory, _entry_node跳过检索

    # --- 实体记忆 + 写闭环(P2, 即插即用) ---
    memory_auto_write: bool = False          # 写闭环开关; False=不沉淀记忆(query.py跳过writer)
    memory_write_interval: int = 5           # A1 写队列worker轮询间隔秒; 0=不启动worker(仅入队不消费)

    # --- 记忆整合(P3, 即插即用) ---
    memory_consolidate_interval: int = 0     # 后台整合间隔秒; 0=不启用后台定时(API手动触发); 如86400=每日
    memory_dedup_threshold: float = 0.88     # 去重相似度阈值; BGE-M3中文短文本cosine偏低, 0.92过严(实测几乎不触发)
    memory_decay_lambda: float = 0.05        # 时间衰减系数 exp(-λ×天); 0.05≈14天半衰, 大者衰减更快
    memory_max_per_user: int = 0             # E1 单用户记忆上限(0=不限); 超限LRU淘汰(access_count低+老)
    memory_context_max_chars: int = 500      # E3 注入prompt的memory_context最大字符(防token膨胀)

    @property
    def mysql_cfg(self) -> MySQLCfg:
        return MySQLCfg(host=self.mysql_host, port=self.mysql_port,
                        db=self.mysql_database or self.mysql_db,
                        user=self.mysql_user, password=self.mysql_password)

    @property
    def qdrant_cfg(self) -> QdrantCfg:
        return QdrantCfg(host=self.qdrant_host, port=self.qdrant_port, api_key=self.qdrant_api_key)

    @property
    def oss_cfg(self) -> OSSCfg:
        return OSSCfg(endpoint=self.oss_endpoint, bucket=self.oss_bucket,
                      access_key_id=self.oss_access_key_id, access_key_secret=self.oss_access_key_secret)

    @property
    def providers(self) -> dict[str, ProviderCfg]:
        """provider前缀→连接信息. ponytail: 从扁平字段派生(零.env迁移); 加新provider=加env字段+此处映射."""
        return {
            "openai": ProviderCfg(api_key=self.litellm_api_key, base_url=self.litellm_base_url),
            "siliconflow": ProviderCfg(api_key=self.embedding_api_key, base_url=self.embedding_base_url),
        }

    @property
    def langfuse_enabled(self) -> bool:
        return bool(self.langfuse_host and self.langfuse_public_key and self.langfuse_secret_key)

    @property
    def redis_url(self) -> str:
        """Redis连接URL. ponytail: 按用途分库(db0记忆/db1查询缓存/db2文档缓存), 密码可选."""
        auth = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}"

    @property
    def memory_config(self):
        """短期记忆治理配置(P0). ponytail: 懒导入避免core循环依赖memory."""
        from src.memory.short_term import MemoryConfig
        return MemoryConfig(
            enabled=self.memory_enabled,
            max_context_tokens=self.memory_max_context_tokens,
            keep_recent_turns=self.memory_keep_recent_turns,
            enable_summary=self.memory_enable_summary,
        )


_settings: Settings | None = None


def get_settings() -> Settings:
    """全局 Settings 单例(懒加载). 所有配置经此取, 不再散落 os.getenv。"""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


if __name__ == "__main__":  # ponytail: 配置加载自检(minimal runnable check)
    s = get_settings()
    assert s.mysql_cfg.port == 3306
    assert s.qdrant_cfg.host  # scheme由QdrantWriter._normalize_host补, settings只载配置
    assert s.litellm_model and s.providers["openai"].base_url
    print(f"settings ok: mysql={s.mysql_cfg.host}:{s.mysql_cfg.port}/{s.mysql_cfg.db} "
          f"qdrant={s.qdrant_cfg.host} llm={s.litellm_model} langfuse={s.langfuse_enabled} "
          f"redis={s.redis_url}")
