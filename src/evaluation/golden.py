# ponytail: 评估文档集(确定性id) + Golden问题(标注relevant doc ids)
# 8主题文档 + 8语义近似干扰项, 考验 dense 能否被噪声误导、hybrid/rerank 能否纠偏

DOCUMENTS = [
    # --- 主题文档(被golden引用) ---
    ("doc_arch", "DeepRAG使用LangGraph构建Agent状态机,实现CRAG纠正式检索增强生成,支持检索-评估-生成-校验的自纠循环。"),
    ("doc_retrieval", "DeepRAG检索层采用BGE-M3密集向量与Qdrant原生BM25稀疏向量,通过RRF融合,再用BGE-Reranker交叉编码器精排。"),
    ("doc_eval", "DeepRAG用Golden Dataset做消融实验,指标含Recall@K、MRR、Faithfulness、Citation Accuracy。"),
    ("doc_vecdb", "Qdrant是向量数据库,支持混合检索、RRF融合、原生BM25稀疏向量与IDF加权。"),
    ("doc_embedding", "BGE-M3是多语言嵌入模型,输出1024维dense向量,支持100多种语言。"),
    ("doc_rag", "RAG是检索增强生成,通过检索外部知识库减少大模型幻觉,提升事实准确性。"),
    ("doc_paris", "巴黎是法国首都,有埃菲尔铁塔、卢浮宫和凯旋门等著名景点。"),
    ("doc_pasta", "意大利面是经典西餐主食,搭配番茄罗勒酱非常美味。"),
    # --- 干扰项(语义近似但golden不引用, 考验区分度) ---
    ("doc_langchain", "LangChain是流行的LLM应用框架,提供链式调用、memory和agent工具集成,用于构建大模型应用。"),
    ("doc_pinecone", "Pinecone是托管型向量数据库,提供serverless语义检索、过滤和大规模近似最近邻搜索。"),
    ("doc_splade", "SPLADE是一种学习型稀疏向量方法,通过MLM生成稀疏词项表示做神经检索。"),
    ("doc_crossencoder", "Cross-encoder将query和document拼接后联合编码打分,常用于RAG的精排重排序阶段。"),
    ("doc_london", "伦敦是英国首都,有大本钟、伦敦塔桥、白金汉宫和大英博物馆。"),
    ("doc_pizza", "比萨饼是意大利经典食物,面饼上铺番茄酱、马苏里拉奶酪和各式配料烤制而成。"),
    ("doc_gpt", "GPT系列是大语言模型,通过预测下一个token生成文本,但在事实性上存在幻觉问题。"),
    ("doc_faiss", "Faiss是Facebook开源的向量检索库,支持近似最近邻搜索、积化和量化压缩。"),
]

GOLDEN = [
    # ponytail: 问题刻意口语化/同义/泛指, 避开原文关键词, 考验dense能否被语义近似干扰项带偏
    {"q": "DeepRAG用什么框架构建状态机?", "relevant": ["doc_arch"]},
    {"q": "DeepRAG如何对检索结果做精排?", "relevant": ["doc_retrieval"]},          # crossencoder干扰
    {"q": "这个系统怎么度量检索和生成的好坏?", "relevant": ["doc_eval"]},            # 口语指代
    {"q": "哪个向量库支持原生稀疏和RRF融合?", "relevant": ["doc_vecdb"]},            # faiss/pinecone干扰
    {"q": "那个输出1024维的多语言嵌入模型是什么?", "relevant": ["doc_embedding"]},   # gpt干扰
    {"q": "检索增强生成为什么能降低幻觉?", "relevant": ["doc_rag"]},                 # gpt干扰
    {"q": "两路召回的结果是怎么合并的?", "relevant": ["doc_retrieval", "doc_vecdb"]},
    {"q": "存向量的系统和编码向量的模型怎么协作?", "relevant": ["doc_vecdb", "doc_embedding"]},
    {"q": "法国首都的著名地标有哪些?", "relevant": ["doc_paris"]},                  # london强干扰
    {"q": "DeepRAG怎样避免模型胡说八道?", "relevant": ["doc_arch", "doc_rag"]},     # 口语化
]
