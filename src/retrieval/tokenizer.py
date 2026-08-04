# ponytail: 中文分词工具(实验性).
# W3.1实验结论: 对当前中文QA数据集, jieba分词后BM25因"共有词噪声"反而拉低hybrid
#   (例: "法国首都"分词为"法国 首都", "首都"与"英国首都"共有, BM25误命中干扰项london);
#   原文(字符级)BM25严格匹配字符序列, 更精确. 故默认sparse用原文.
# 分词适用于术语密集/中英混排/领域OOOD场景, 需要时在入库sparse_text与检索query同时启用本函数.

import jieba


def tokenize_zh(text: str) -> str:
    """jieba分词后空格连接, 供BM25 sparse使用. dense embedding仍用原文."""
    return " ".join(t for t in jieba.cut(text) if t.strip())
