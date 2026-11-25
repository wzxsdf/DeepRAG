# 用户偏好学习 - 个性化检索和推荐

from datetime import datetime
from typing import Dict, List, Any
import json

from src.core.logging import log


class UserProfile:
    """用户偏好模型 - 学习用户行为模式"""

    def __init__(
        self,
        user_id: str,
        preferences: Dict[str, Any] = None,
        interaction_history: List[Dict] = None,
        created_at: datetime = None,
        updated_at: datetime = None
    ):
        self.user_id = user_id
        self.preferences = preferences or {
            # 检索偏好
            "preferred_domains": [],  # 偏好的领域/主题
            "query_patterns": [],  # 常用查询模式
            "response_length": "medium",  # 喜欢的回复长度
            "detail_level": "balanced",  # 信息详细程度

            # 内容偏好
            "topics_of_interest": [],  # 感兴趣的主题
            "ignored_topics": [],  # 忽略的主题
            "preferred_sources": [],  # 偏好的信息源

            # 交互偏好
            "interface_mode": "standard",  # 界面模式
            "language": "zh-CN",  # 语言偏好
            "timezone": "Asia/Shanghai"  # 时区
        }
        self.interaction_history = interaction_history or []
        self.created_at = created_at or datetime.now()
        self.updated_at = updated_at or datetime.now()

    def add_interaction(self, interaction: Dict[str, Any]) -> None:
        """添加交互记录

        Args:
            interaction: 交互数据
        """
        interaction["timestamp"] = datetime.now().isoformat()
        self.interaction_history.append(interaction)
        # 只保留最近100次交互
        if len(self.interaction_history) > 100:
            self.interaction_history = self.interaction_history[-100:]
        self.updated_at = datetime.now()

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "user_id": self.user_id,
            "preferences": self.preferences,
            "interaction_count": len(self.interaction_history),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat()
        }


class UserPreferenceLearner:
    """用户偏好学习器 - 从交互中学习用户偏好"""

    def __init__(self, redis_client=None, mysql_store=None):
        self.redis_client = redis_client
        self.mysql_store = mysql_store
        self.profiles_cache: Dict[str, UserProfile] = {}

    async def learn_from_feedback(
        self,
        user_id: str,
        query: str,
        result: Dict[str, Any],
        feedback: Dict[str, Any]
    ) -> None:
        """从用户反馈中学习偏好

        Args:
            user_id: 用户ID
            query: 查询内容
            result: 系统结果
            feedback: 用户反馈 {
                "rating": int,  # 1-5评分
                "selected_docs": List[str],  # 用户选择的文档
                "reading_time": float,  # 阅读时间(秒)
                "follow_up_questions": List[str],  # 后续问题
                "explicit_feedback": str  # 明确反馈
            }
        """
        profile = await self.get_profile(user_id)

        # 1. 从评分学习质量偏好
        rating = feedback.get("rating", 0)
        if rating >= 4:
            # 高分反馈：强化当前策略
            await self._reinforce_preferences(profile, query, result)
        elif rating <= 2:
            # 低分反馈：调整策略
            await self._adjust_preferences(profile, query, result, feedback)

        # 2. 从文档选择学习内容偏好
        selected_docs = feedback.get("selected_docs", [])
        if selected_docs:
            await self._learn_content_preferences(profile, selected_docs, result)

        # 3. 从阅读时间学习详细程度偏好
        reading_time = feedback.get("reading_time", 0)
        if reading_time > 0:
            await self._learn_detail_preference(profile, reading_time, result)

        # 4. 从后续问题学习兴趣点
        follow_up = feedback.get("follow_up_questions", [])
        if follow_up:
            await self._learn_topic_interests(profile, follow_up)

        # 5. 更新交互历史
        profile.add_interaction({
            "query": query,
            "result_summary": self._summarize_result(result),
            "feedback": feedback
        })

        # 6. 保存更新
        await self._save_profile(profile)

        log.info(
            "preferences_learned",
            user_id=user_id,
            rating=rating,
            interaction_count=len(profile.interaction_history)
        )

    async def _reinforce_preferences(
        self,
        profile: UserProfile,
        query: str,
        result: Dict[str, Any]
    ) -> None:
        """强化当前偏好

        Args:
            profile: 用户配置
            query: 查询内容
            result: 系统结果
        """
        # 提取查询中的关键词
        keywords = self._extract_keywords(query)
        profile.preferences["query_patterns"].extend(keywords)

        # 提取结果中的主题
        topics = self._extract_topics(result)
        profile.preferences["topics_of_interest"].extend(topics)

    async def _adjust_preferences(
        self,
        profile: UserProfile,
        query: str,
        result: Dict[str, Any],
        feedback: Dict[str, Any]
    ) -> None:
        """调整偏好策略

        Args:
            profile: 用户配置
            query: 查询内容
            result: 系统结果
            feedback: 用户反馈
        """
        explicit_feedback = feedback.get("explicit_feedback", "")
        if explicit_feedback:
            # 分析反馈内容
            if "太长" in explicit_feedback or "冗长" in explicit_feedback:
                profile.preferences["response_length"] = "concise"
            elif "太短" in explicit_feedback or "不够详细" in explicit_feedback:
                profile.preferences["response_length"] = "detailed"
            elif "太简单" in explicit_feedback or "不够深入" in explicit_feedback:
                profile.preferences["detail_level"] = "comprehensive"
            elif "太复杂" in explicit_feedback or "过于详细" in explicit_feedback:
                profile.preferences["detail_level"] = "simple"

    async def _learn_content_preferences(
        self,
        profile: UserProfile,
        selected_docs: List[str],
        result: Dict[str, Any]
    ) -> None:
        """学习内容偏好

        Args:
            profile: 用户配置
            selected_docs: 用户选择的文档ID
            result: 系统结果
        """
        # 提取选中文档的特征
        all_docs = result.get("documents", [])
        selected_set = set(selected_docs)

        for doc in all_docs:
            doc_id = doc.get("id")
            if doc_id in selected_set:
                # 用户选择了这个文档，学习其特征
                domain = doc.get("metadata", {}).get("domain")
                source = doc.get("metadata", {}).get("source")
                topic = doc.get("metadata", {}).get("topic")

                if domain and domain not in profile.preferences["preferred_domains"]:
                    profile.preferences["preferred_domains"].append(domain)

                if source and source not in profile.preferences["preferred_sources"]:
                    profile.preferences["preferred_sources"].append(source)

                if topic and topic not in profile.preferences["topics_of_interest"]:
                    profile.preferences["topics_of_interest"].append(topic)

    async def _learn_detail_preference(
        self,
        profile: UserProfile,
        reading_time: float,
        result: Dict[str, Any]
    ) -> None:
        """学习详细程度偏好

        Args:
            profile: 用户配置
            reading_time: 阅读时间
            result: 系统结果
        """
        answer_length = len(result.get("answer", ""))

        # 计算阅读速度 (字符/秒)
        reading_speed = answer_length / reading_time if reading_time > 0 else 0

        if reading_speed < 50:  # 慢读，可能喜欢详细内容
            profile.preferences["detail_level"] = "comprehensive"
        elif reading_speed > 200:  # 快速阅读，可能喜欢简洁
            profile.preferences["detail_level"] = "concise"

    async def _learn_topic_interests(
        self,
        profile: UserProfile,
        follow_up_questions: List[str]
    ) -> None:
        """学习主题兴趣

        Args:
            profile: 用户配置
            follow_up_questions: 后续问题列表
        """
        for question in follow_up_questions:
            topics = self._extract_keywords(question)
            profile.preferences["topics_of_interest"].extend(topics)

    async def get_profile(self, user_id: str) -> UserProfile:
        """获取用户配置

        Args:
            user_id: 用户ID

        Returns:
            UserProfile: 用户配置对象
        """
        # 先从缓存查找
        if user_id in self.profiles_cache:
            return self.profiles_cache[user_id]

        # 从Redis加载
        if self.redis_client:
            redis_key = f"user_profile:{user_id}"
            profile_json = await self.redis_client.get(redis_key)
            if profile_json:
                try:
                    profile_data = json.loads(profile_json)
                    profile = UserProfile(
                        user_id=user_id,
                        preferences=profile_data.get("preferences"),
                        interaction_history=profile_data.get("interaction_history", []),
                        created_at=datetime.fromisoformat(profile_data.get("created_at")),
                        updated_at=datetime.fromisoformat(profile_data.get("updated_at"))
                    )
                    self.profiles_cache[user_id] = profile
                    return profile
                except Exception as e:
                    log.warning("failed_to_load_profile", user_id=user_id, error=str(e))

        # 创建新配置
        profile = UserProfile(user_id=user_id)
        self.profiles_cache[user_id] = profile
        return profile

    async def _save_profile(self, profile: UserProfile) -> None:
        """保存用户配置

        Args:
            profile: 用户配置对象
        """
        profile.updated_at = datetime.now()

        # 保存到Redis
        if self.redis_client:
            redis_key = f"user_profile:{profile.user_id}"
            try:
                await self.redis_client.setex(
                    redis_key,
                    3600 * 24 * 30,  # 30天过期
                    json.dumps(profile.to_dict())
                )
            except Exception as e:
                log.warning("failed_to_save_profile", user_id=profile.user_id, error=str(e))

        # 异步保存到MySQL（如果需要持久化）
        if self.mysql_store:
            await self._save_to_mysql(profile)

    async def _save_to_mysql(self, profile: UserProfile) -> None:
        """保存到MySQL

        Args:
            profile: 用户配置对象
        """
        try:
            await self.mysql_store.execute(
                """INSERT INTO user_profiles
                   (user_id, preferences, interaction_count, created_at, updated_at)
                   VALUES (%s, %s, %s, %s, %s)
                   ON DUPLICATE KEY UPDATE
                   preferences = VALUES(preferences),
                   interaction_count = VALUES(interaction_count),
                   updated_at = VALUES(updated_at)""",
                profile.user_id,
                json.dumps(profile.preferences),
                len(profile.interaction_history),
                profile.created_at,
                profile.updated_at
            )
        except Exception as e:
            log.warning("failed_to_save_profile_mysql", user_id=profile.user_id, error=str(e))

    async def personalize_results(
        self,
        user_id: str,
        results: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """个性化结果排序

        Args:
            user_id: 用户ID
            results: 检索结果列表

        Returns:
            List[Dict]: 个性化排序后的结果
        """
        profile = await self.get_profile(user_id)

        # 为每个结果计算个性化分数
        scored_results = []
        for result in results:
            base_score = result.get("score", 0.0)
            personal_boost = await self._calculate_personal_boost(result, profile)
            final_score = base_score + personal_boost

            scored_results.append({
                **result,
                "personalized_score": final_score,
                "personal_boost": personal_boost
            })

        # 按个性化分数重新排序
        scored_results.sort(key=lambda x: x["personalized_score"], reverse=True)

        return scored_results

    async def _calculate_personal_boost(
        self,
        result: Dict[str, Any],
        profile: UserProfile
    ) -> float:
        """计算个性化提升分数

        Args:
            result: 检索结果
            profile: 用户配置

        Returns:
            float: 个性化提升分数
        """
        boost = 0.0
        metadata = result.get("metadata", {})

        # 1. 领域偏好匹配
        domain = metadata.get("domain")
        if domain in profile.preferences["preferred_domains"]:
            boost += 0.1

        # 2. 来源偏好匹配
        source = metadata.get("source")
        if source in profile.preferences["preferred_sources"]:
            boost += 0.05

        # 3. 主题偏好匹配
        topic = metadata.get("topic")
        if topic in profile.preferences["topics_of_interest"]:
            boost += 0.08

        # 4. 避免忽略的主题
        if topic in profile.preferences.get("ignored_topics", []):
            boost -= 0.2

        return min(boost, 0.3)  # 最大提升0.3分

    def _extract_keywords(self, text: str) -> List[str]:
        """提取关键词

        Args:
            text: 输入文本

        Returns:
            List[str]: 关键词列表
        """
        # 简单实现：分词+过滤
        # 实际应用中可以用更复杂的NLP
        import jieba
        words = jieba.cut(text)
        # 过滤停用词和短词
        stopwords = {"的", "是", "在", "和", "与", "或", "等", "了", "有", "不"}
        keywords = [w for w in words if len(w) > 1 and w not in stopwords]
        return list(set(keywords))[:5]  # 返回前5个关键词

    def _extract_topics(self, result: Dict[str, Any]) -> List[str]:
        """提取结果主题

        Args:
            result: 系统结果

        Returns:
            List[str]: 主题列表
        """
        metadata = result.get("metadata", {})
        topics = []

        # 从各个可能的字段提取主题
        for field in ["topic", "domain", "category", "tags"]:
            value = metadata.get(field)
            if value:
                if isinstance(value, list):
                    topics.extend(value)
                elif isinstance(value, str):
                    topics.append(value)

        return list(set(topics))

    def _summarize_result(self, result: Dict[str, Any]) -> str:
        """总结结果

        Args:
            result: 系统结果

        Returns:
            str: 结果摘要
        """
        answer = result.get("answer", "")
        return answer[:100] + "..." if len(answer) > 100 else answer


# 全局单例
_preference_learner = None


def get_preference_learner(redis_client=None, mysql_store=None) -> UserPreferenceLearner:
    """获取偏好学习器单例

    Args:
        redis_client: Redis客户端
        mysql_store: MySQL存储实例

    Returns:
        UserPreferenceLearner: 偏好学习器实例
    """
    global _preference_learner

    if _preference_learner is None:
        _preference_learner = UserPreferenceLearner(
            redis_client=redis_client,
            mysql_store=mysql_store
        )

    return _preference_learner


__all__ = [
    "UserProfile",
    "UserPreferenceLearner",
    "get_preference_learner"
]
