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
          f"qdrant={s.qdrant_cfg.host} llm={s.litellm_model} langfuse={s.langfuse_enabled}")
