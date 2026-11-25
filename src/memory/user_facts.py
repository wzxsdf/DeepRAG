# 实体记忆存储组件 — P2 核心
#
# 职责: 结构化用户事实/偏好的持久化(实体记忆层), MySQL 存储, 精确查询(非语义检索)。
# 解耦: 纯组件, 依赖 MySQLStore 执行层(注入), 不依赖 graph/agents/api。
# 即插即用: 由 lifespan 按配置注入; 不注入则 graph 读侧/query 写侧自动跳过。
#
# C1: key 归一化(同义词→标准key, 防碎片化); G1: 敏感信息过滤(密码/token等不入库)。
# 对应设计文档 docs/MEMORY_LAYER_DESIGN.md §4.2(实体记忆 user_facts 表) + §6.1(结构化抽取)。


from src.core.logging import log


# C1: 标准key → 同义词列表(归一化映射, 防 preferred_lang/language_pref 等碎片化)
_FACT_KEY_ALIASES: dict[str, list[str]] = {
    "preferred_language": ["preferred_lang", "language_pref", "prog_lang", "language", "编程语言", "语言偏好", "lang"],
    "framework": ["api_framework", "web_framework", "框架", "tech_stack", "技术栈"],
    "role": ["job", "position", "职位", "角色", "职业", "title"],
    "timezone": ["时区", "tz"],
    "current_project": ["project", "当前项目", "项目", "project_name"],
    "domain": ["领域", "field", "行业"],
    "experience_level": ["level", "水平", "经验", "seniority"],
}

# G1: key 含这些词 → 视为敏感, 拒绝写入
_SENSITIVE_KEY_KEYWORDS = {"password", "passwd", "secret", "token", "apikey", "api_key",
                           "credential", "key", "身份证", "银行卡", "手机号"}


def normalize_key(key: str) -> str:
    """C1: fact_key 归一化。小写+下划线 + 同义词映射到标准key。

    例: "Language_Pref" / "编程语言" / "prog_lang" → "preferred_language"
    """
    if not key:
        return key
    k = key.strip().lower().replace("-", "_").replace(" ", "_")
    for std, aliases in _FACT_KEY_ALIASES.items():
        if k == std or k in [a.lower() for a in aliases]:
            return std
    return k


def _is_sensitive(key: str, value: str) -> bool:
    """G1: 敏感检测。key 含敏感词 或 value 像密钥(长且高熵) → 拒绝写入。"""
    k = (key or "").lower()
    if any(w in k for w in _SENSITIVE_KEY_KEYWORDS):
        return True
    v = value or ""
    # ponytail: 启发式 — 长(>32)且无空格且高字母数字占比 → 疑似token/apikey
    if len(v) > 32 and " " not in v:
        alnum_ratio = sum(c.isalnum() for c in v) / len(v)
        if alnum_ratio > 0.85:
            return True
    return False


class UserFactStore:
    """用户事实存储 — 即插即用组件。

    用法:
        store = UserFactStore(mysql_store)
        await store.ensure_schema()
        await store.upsert("user-1", "preferred_language", "Python")
        facts = await store.get_all("user-1")  # {"preferred_language": "Python", ...}

    存储结构(MySQL user_facts 表):
        (user_id, fact_key) UNIQUE → 同 key 覆盖更新(自动冲突消解, 留新不留旧)
    """

    def __init__(self, mysql_store):
        self._m = mysql_store

    async def ensure_schema(self):
        """幂等建表。lifespan 启动调用, 替代运行时 DDL。"""
        await self._m.execute(
            "CREATE TABLE IF NOT EXISTS user_facts ("
            "id BIGINT AUTO_INCREMENT PRIMARY KEY, "
            "user_id VARCHAR(50) NOT NULL, "
            "fact_key VARCHAR(100) NOT NULL, "
            "fact_value TEXT NOT NULL, "
            "confidence FLOAT DEFAULT 1.0, "
            "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, "
            "updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP, "
            "UNIQUE KEY uk_user_fact (user_id, fact_key), "
            "INDEX idx_user (user_id)"
            ") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4"
        )

    async def upsert(self, user_id: str, key: str, value: str, confidence: float = 1.0) -> None:
        """写入/覆盖一条事实。UNIQUE KEY(user_id, fact_key) → 同 key 自动覆盖(冲突消解)。

        C1: key 先归一化(同义词→标准); G1: 敏感信息跳过不入库。
        ponytail: ON DUPLICATE KEY UPDATE(别名语法) 实现幂等 upsert。
        """
        if not user_id or not key or value is None:
            return
        key = normalize_key(key)  # C1 归一化
        value = str(value)
        if _is_sensitive(key, value):  # G1 敏感过滤
            log.warning("user_fact_sensitive_skipped", user_id=user_id, key=key)
            return
        try:
            await self._m.execute(
                "INSERT INTO user_facts (user_id, fact_key, fact_value, confidence) "
                "VALUES (%s, %s, %s, %s) AS new "
                "ON DUPLICATE KEY UPDATE fact_value=new.fact_value, confidence=new.confidence, "
                "updated_at=CURRENT_TIMESTAMP",
                user_id, key, value, confidence,
            )
        except Exception as e:
            log.warning("user_fact_upsert_failed", user_id=user_id, key=key, error=str(e))

    async def get_all(self, user_id: str) -> dict[str, str]:
        """读取某用户全部事实。会话开始一次性加载, 注入 prompt 作为用户画像。

        Returns:
            dict: {fact_key: fact_value}
        """
        try:
            rows = await self._m.fetch(
                "SELECT fact_key, fact_value FROM user_facts WHERE user_id=%s", user_id
            )
            return {r["fact_key"]: r["fact_value"] for r in rows}
        except Exception as e:
            log.warning("user_fact_load_failed", user_id=user_id, error=str(e))
            return {}

    async def delete(self, user_id: str, key: str) -> None:
        """删除单条事实。"""
        try:
            await self._m.execute(
                "DELETE FROM user_facts WHERE user_id=%s AND fact_key=%s", user_id, key
            )
        except Exception as e:
            log.warning("user_fact_delete_failed", user_id=user_id, key=key, error=str(e))

    async def delete_by_user(self, user_id: str) -> None:
        """删除某用户全部事实(隐私/注销)。幂等。"""
        try:
            await self._m.execute("DELETE FROM user_facts WHERE user_id=%s", user_id)
        except Exception as e:
            log.warning("user_fact_clear_failed", user_id=user_id, error=str(e))

    async def count(self) -> int:
        """事实总数(F1 指标用)。表不存在等异常返回 0。"""
        try:
            r = await self._m.fetchrow("SELECT COUNT(*) AS c FROM user_facts")
            return r["c"] if r else 0
        except Exception:
            return 0


__all__ = ["UserFactStore"]
