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

    # --- Langfuse(LLM trace, 阶段2接ModelGateway); 三key齐全即启用 ---
    langfuse_host: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    # --- Redis(记忆模块+缓存系统, 阶段W4) ---
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""
    redis_db: int = 0  # ponytail: 记忆db0/查询缓存db1/文档缓存db2, 实例级单URL按用途分库

    # --- 短期记忆治理(P0, 即插即用) ---
    memory_enabled: bool = True              # 总开关; False=组件直通(不裁剪不摘要, 兼容旧行为)
    memory_max_context_tokens: int = 2000    # 历史占用token预算; 超过才触发摘要+窗口
    memory_keep_recent_turns: int = 4        # 触发压缩时, 近期保留的原文轮数
    memory_enable_summary: bool = True       # 是否启用LLM摘要; False=超预算时仅硬截断(纯窗口)

    # --- 长期记忆(P1, 即插即用) ---
    memory_long_term_enabled: bool = True    # 长期记忆读闭环开关; False=不建agent_memory, _entry_node跳过检索

    # --- 实体记忆 + 写闭环(P2, 即插即用) ---
    memory_auto_write: bool = True           # 写闭环开关; False=不沉淀记忆(query.py跳过writer)
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
