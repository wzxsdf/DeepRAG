# 知识库检索工具 - 优化版

from typing import List, Optional
from pydantic import BaseModel, Field

from langchain_core.tools import tool
from langchain_core.runnables import RunnableConfig


# ponytail: 检索结果结构化输出
class KBSearchResult(BaseModel):
    """知识库检索结果结构化输出"""
    query: str = Field(description="检索查询")
    results_count: int = Field(description="结果数量")
    documents: List[str] = Field(default_factory=list, description="检索到的文档片段")
    success: bool = Field(description="是否成功")
    error: Optional[str] = Field(default=None, description="错误信息")


@tool
async def search_kb_tool(query: str, config: RunnableConfig) -> str:
    """在知识库检索与查询相关的文档片段。

    当需要查找知识库中的事实证据(如简历内容、上传文档、公司资料等)时调用此工具。
    通过向量检索和重排序返回最相关的文档片段。

    Args:
        query: 检索查询，应该使用关键词或简洁的问题
        config: 运行时配置，自动注入retriever、tenant_id、kb_ids等参数

    Returns:
        str: 检索结果，每个文档片段显示前150字符，或错误信息

    Examples:
        >>> await search_kb_tool.invoke("张三的工作经历", config={...})
        "- 张三，2018-2020年在ABC公司担任软件工程师..."
        "- 张三，2020年至今在XYZ公司担任技术主管..."

        >>> await search_kb_tool.invoke("公司产品线", config={...})
        "- 公司主要产品包括AI助手、数据分析平台..."
        "- 产品客户群体覆盖金融、医疗、教育等领域..."
    """
    from src.storage.protocol import RetrievalFilter
    from src.retrieval.reranker import rerank

    cfg = (config or {}).get("configurable", {})
    retriever = cfg.get("retriever")

    if retriever is None:
        return "(检索器未配置)"

    tenant_id = cfg.get("tenant_id", "default")
    kb_ids = cfg.get("kb_ids")

    try:
        # 检索：top_k=5
        docs = await retriever.retrieve(
            query,
            RetrievalFilter(tenant_id=tenant_id, kb_ids=kb_ids),
            top_k=5
        )

        # 重排序：top_n=3
        docs = await rerank(query, docs, top_n=3)

        # 格式化结果
        results = []
        for doc in docs:
            content = doc.content[:150]  # 前150字符
            if len(doc.content) > 150:
                content += "..."
            results.append(f"- {content}")

        return "\n".join(results) or "(无结果)"

    except Exception as e:
        return f"(检索失败: {type(e).__name__})"


__all__ = ["search_kb_tool", "KBSearchResult"]
