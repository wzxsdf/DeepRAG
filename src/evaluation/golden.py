# ponytail: 评估文档集(确定性id) + Golden问题(标注relevant doc ids)
# golden v2(20261125定版): 24主题文档 + 24语义近似干扰项, 54问(v1的10问原样保留 + 新增44问)
# 设计规则:
#   1. 每个主题文档是自包含短段落, 关键事实落在前150字符内(kb模式证据截断窗口)
#   2. 干扰项与主题语义近似但不作为任何问题的relevant, 考验 dense 被噪声误导、hybrid/rerank 纠偏
#   3. v1语料16文档时 top_k=20 能捞回全库, 候选R@20被饱和成1.0(假信号); v2扩到48文档后 top20≈42%池子, headroom读数才有意义
#   4. 问题刻意口语化/同义/泛指, 避开原文关键词; relevant 只允许主题文档, 每问1~3个

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
    ("doc_redis", "Redis是开源内存键值数据库,常用作缓存、排行榜和消息中间件,支持字符串、哈希、列表、集合等丰富数据结构。"),
    ("doc_celery", "Celery是Python分布式任务队列,通过Redis或RabbitMQ作为broker异步执行后台任务,支持失败自动重试与定时调度。"),
    ("doc_mysql", "MySQL是开源关系型数据库,使用InnoDB引擎支持事务、行级锁与ACID,广泛用于订单、库存等强一致数据存储。"),
    ("doc_oss", "阿里云OSS是对象存储服务,通过Bucket存放图片、文档等非结构化文件,提供HTTPS直传与签名URL临时下载。"),
    ("doc_langfuse", "Langfuse是开源LLM应用观测平台,追踪全链路trace、评估生成质量与管理prompt版本,支持自托管部署。"),
    ("doc_tokyo", "东京是日本首都,以涩谷十字路口、东京塔和浅草寺闻名,涩谷路口被称为世界上最繁忙的十字路口。"),
    ("doc_sushi", "寿司是日本经典料理,醋饭搭配生鱼片、海苔或蔬菜制成,江户前寿司以金枪鱼和玉子烧为代表食材。"),
    ("doc_docker", "Docker是容器化平台,将应用与依赖打包成镜像,以容器为单位隔离运行,镜像通过分层文件系统构建。"),
    ("doc_fastapi", "FastAPI是基于Python类型标注的现代Web框架,自动生成OpenAPI接口文档,原生支持async异步请求处理。"),
    ("doc_structlog", "structlog是Python结构化日志库,以键值对输出机器可解析日志,便于集中采集与检索分析。"),
    ("doc_jieba", "jieba是Python中文分词库,基于前缀词典与动态规划实现精确模式切分,支持自定义词典。"),
    ("doc_hangzhou", "杭州是浙江省会,以西湖十景闻名,京杭大运河流经此地,阿里巴巴总部坐落于此。"),
    ("doc_nio", "蔚来是中国智能电动汽车品牌,主打换电站补能模式,以NIO House用户社区著称,旗下有ET5和ES6等车型。"),
    ("doc_milkyway", "银河系是包含太阳系的棒旋星系,直径约10万光年,恒星数量在1000亿到4000亿颗之间。"),
    ("doc_panda", "大熊猫是中国特有珍稀动物,以竹子为主食,栖息在四川、陕西和甘肃的高山竹林中。"),
    ("doc_forbidden_city", "故宫又称紫禁城,是明清两代皇宫,位于北京中轴线核心,是现存规模最大的古代宫殿建筑群。"),
    # --- 干扰项(语义近似但golden不引用, 考验区分度) ---
    ("doc_langchain", "LangChain是流行的LLM应用框架,提供链式调用、memory和agent工具集成,用于构建大模型应用。"),
    ("doc_pinecone", "Pinecone是托管型向量数据库,提供serverless语义检索、过滤和大规模近似最近邻搜索。"),
    ("doc_splade", "SPLADE是一种学习型稀疏向量方法,通过MLM生成稀疏词项表示做神经检索。"),
    ("doc_crossencoder", "Cross-encoder将query和document拼接后联合编码打分,常用于RAG的精排重排序阶段。"),
    ("doc_london", "伦敦是英国首都,有大本钟、伦敦塔桥、白金汉宫和大英博物馆。"),
    ("doc_pizza", "比萨饼是意大利经典食物,面饼上铺番茄酱、马苏里拉奶酪和各式配料烤制而成。"),
    ("doc_gpt", "GPT系列是大语言模型,通过预测下一个token生成文本,但在事实性上存在幻觉问题。"),
    ("doc_faiss", "Faiss是Facebook开源的向量检索库,支持近似最近邻搜索、积化和量化压缩。"),
    ("doc_memcached", "Memcached是分布式内存缓存系统,采用多线程架构,只支持简单的键值字符串缓存,无内置持久化。"),
    ("doc_kafka", "Kafka是分布式流处理平台,以高吞吐分区日志支撑事件流与日志管道,不负责任务重试调度。"),
    ("doc_mongodb", "MongoDB是文档型NoSQL数据库,以BSON文档存储数据,牺牲部分事务能力换取模式灵活性。"),
    ("doc_s3", "Amazon S3是AWS的对象存储服务,按桶组织对象,以11个9的持久性著称,是众多云存储的事实标准。"),
    ("doc_langsmith", "LangSmith是LangChain官方的LLM应用开发平台,提供数据集、评估与监控,面向LangChain生态。"),
    ("doc_seoul", "首尔是韩国首都,以景福宫、明洞商业街和N首尔塔著称,汉江穿城而过。"),
    ("doc_ramen", "拉面是日本流行面食,以豚骨、味噌或酱油汤底配碱水面条,叉烧与溏心蛋是常见配料。"),
    ("doc_kubernetes", "Kubernetes是容器编排系统,负责集群中容器的调度、扩缩容与自愈,常被称为K8s。"),
    ("doc_flask", "Flask是轻量级Python Web框架,以简洁和扩展生态著称,默认同步处理请求。"),
    ("doc_logging", "logging是Python标准库日志模块,以字符串格式化输出为主,需自行封装才能产生结构化字段。"),
    ("doc_pkuseg", "pkuseg是北大开源的中文分词工具,按领域细分模型,支持新闻、网络等领域文本切分。"),
    ("doc_suzhou", "苏州以园林甲天下,拙政园、留园位列中国四大名园,古城区依水而建。"),
    ("doc_tesla", "特斯拉是美国电动汽车厂商,以超充网络和Autopilot辅助驾驶闻名,Model 3/Y主销全球。"),
    ("doc_andromeda", "仙女座星系是距银河系约254万光年的旋涡星系,正以每秒110公里的速度接近银河系。"),
    ("doc_koala", "考拉是澳大利亚有袋类动物,以桉树叶为食,每天睡眠可达20小时。"),
    ("doc_potala", "布达拉宫位于西藏拉萨红山之上,是藏传佛教宫堡建筑群,始建于吐蕃松赞干布时期。"),
]

# 主题/干扰项划分(仅供报告统计口径, eval逻辑不依赖)
DISTRACTOR_IDS = frozenset({
    "doc_langchain", "doc_pinecone", "doc_splade", "doc_crossencoder", "doc_london", "doc_pizza",
    "doc_gpt", "doc_faiss", "doc_memcached", "doc_kafka", "doc_mongodb", "doc_s3", "doc_langsmith",
    "doc_seoul", "doc_ramen", "doc_kubernetes", "doc_flask", "doc_logging", "doc_pkuseg",
    "doc_suzhou", "doc_tesla", "doc_andromeda", "doc_koala", "doc_potala",
})

GOLDEN_VERSION = "v2"

GOLDEN = [
    # --- v1的10问原样保留(基线可对比) ---
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
    # --- v2新增(覆盖全部24主题, 每主题至少2问; 多relevant跨簇联动问在末尾) ---
    {"q": "DeepRAG里那个检索-评估-再检索的自纠循环在哪个模块实现?", "relevant": ["doc_arch"]},
    {"q": "这个项目稀疏检索的词项权重是数据库侧算的吗?", "relevant": ["doc_vecdb", "doc_retrieval"]},  # splade干扰: 学习型稀疏在模型侧
    {"q": "想知道生成的答案是不是都基于检索到的原文,该看哪个指标?", "relevant": ["doc_eval"]},
    {"q": "引用来源对不对得上该怎么评估?", "relevant": ["doc_eval"]},
    {"q": "外部知识接进来模型就不容易瞎编了,这思路叫什么?", "relevant": ["doc_rag"]},
    {"q": "哪个内存数据库既能当缓存又能做排行榜?", "relevant": ["doc_redis"]},       # memcached干扰
    {"q": "想存哈希、列表这些复杂结构做缓存,选那个只支持字符串的还是支持多结构的?", "relevant": ["doc_redis"]},
    {"q": "Python后端的异步后台任务用什么组件跑?", "relevant": ["doc_celery"]},      # kafka干扰
    {"q": "失败任务自动重试和定时调度是谁负责的?", "relevant": ["doc_celery"]},      # kafka明说不管重试
    {"q": "订单库存这种要事务和行级锁的数据放哪类数据库?", "relevant": ["doc_mysql"]},  # mongodb干扰
    {"q": "强一致的表结构和灵活的文档存储之间,关系型那条路线用哪个?", "relevant": ["doc_mysql"]},
    {"q": "用户上传的图片和PDF原文件落在哪个云服务?", "relevant": ["doc_oss"]},      # s3干扰
    {"q": "哪个对象存储能给前端签发临时的下载链接?", "relevant": ["doc_oss"]},
    {"q": "排查一次问答全链路调用用什么观测平台?", "relevant": ["doc_langfuse"]},    # langsmith干扰
    {"q": "能自托管部署、还管prompt版本的LLM观测工具是哪个?", "relevant": ["doc_langfuse"]},
    {"q": "涩谷那个全世界最忙的十字路口在哪个首都?", "relevant": ["doc_tokyo"]},     # seoul干扰
    {"q": "日本首都看见的那座标志性铁塔叫什么?", "relevant": ["doc_tokyo"]},
    {"q": "醋饭顶着生鱼片的那道日本料理是什么?", "relevant": ["doc_sushi"]},         # ramen干扰
    {"q": "金枪鱼和玉子烧是哪道料理江户前流派的代表食材?", "relevant": ["doc_sushi"]},
    {"q": "把应用连同依赖打包成镜像隔离跑起来的技术叫什么?", "relevant": ["doc_docker"]},  # k8s干扰
    {"q": "镜像的分层文件系统是哪个技术带来的?", "relevant": ["doc_docker"]},
    {"q": "写接口自动出OpenAPI文档的Python Web框架选谁?", "relevant": ["doc_fastapi"]},  # flask干扰
    {"q": "想要原生async的现代Web框架,不选默认同步的那个老框架,该用谁?", "relevant": ["doc_fastapi"]},
    {"q": "机器可解析的键值对结构化日志用哪个库?", "relevant": ["doc_structlog"]},   # std logging干扰
    {"q": "中文分词在项目里用的什么库?", "relevant": ["doc_jieba"]},                 # pkuseg干扰
    {"q": "支持自定义词典、动态规划切分的分词器是哪个?", "relevant": ["doc_jieba"]},
    {"q": "西湖十景在哪座城市?", "relevant": ["doc_hangzhou"]},                     # suzhou园林干扰
    {"q": "京杭大运河流经的哪座城市以一片湖闻名?", "relevant": ["doc_hangzhou"]},
    {"q": "主打换电站补能的中国车企是哪家?", "relevant": ["doc_nio"]},               # tesla超充干扰
    {"q": "ET5和ES6是哪个牌子的车?", "relevant": ["doc_nio"]},
    {"q": "太阳系呆在哪个星系里?", "relevant": ["doc_milkyway"]},                   # andromeda干扰
    {"q": "直径十万光年、装着上千亿颗恒星的棒旋星系是?", "relevant": ["doc_milkyway"]},
    {"q": "吃竹子的中国国宝是谁?", "relevant": ["doc_panda"]},                      # koala干扰
    {"q": "大熊猫主要蹲在哪些省的高山竹林里?", "relevant": ["doc_panda"]},
    {"q": "紫禁城住过哪两个朝代的皇帝?", "relevant": ["doc_forbidden_city"]},        # potala宫殿干扰
    {"q": "现存规模最大的古代宫殿建筑群是哪座?", "relevant": ["doc_forbidden_city"]},
    # --- v2跨簇联动问(多relevant) ---
    {"q": "缓存热点数据的内存库和扛异步任务的队列,项目里分别用的什么?", "relevant": ["doc_redis", "doc_celery"]},
    {"q": "文件解析完的结构化数据进哪个数据库?分词又靠哪个库?", "relevant": ["doc_mysql", "doc_jieba"]},
    {"q": "Web框架、任务队列、缓存三件套分别是什么?", "relevant": ["doc_fastapi", "doc_celery", "doc_redis"]},
    {"q": "LLM调用链观测平台和结构化日志库分别是哪两个?", "relevant": ["doc_langfuse", "doc_structlog"]},
    {"q": "日本和法国的首都各自最著名的地标是什么?", "relevant": ["doc_tokyo", "doc_paris"]},
    {"q": "向量存储、Web框架、观测平台的项目选型分别是?", "relevant": ["doc_vecdb", "doc_fastapi", "doc_langfuse"]},
    {"q": "意面配什么酱、寿司饭用什么调的味?", "relevant": ["doc_pasta", "doc_sushi"]},
    {"q": "哪道西餐主食的经典吃法是配番茄罗勒酱?", "relevant": ["doc_pasta"]},   # pizza番茄酱干扰
]
