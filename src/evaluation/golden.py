# ponytail: 评估文档集(确定性id) + Golden问题(标注relevant doc ids + 参考答案) + 不可答负样本
# golden v5(20261129定版): v4的100问原样保留 + 新增20个多轮问(带"history"字段) = 120问 + UNANSWERABLE 18问
# 设计规则:
#   1. 每个主题文档是自包含短段落, 关键事实落在前150字符内(kb模式证据截断窗口)
#   2. 干扰项与主题语义近似但不作为任何问题的relevant, 考验 dense 被噪声误导、hybrid/rerank 纠偏
#   3. v1语料16文档时 top_k=20 能捞回全库, 候选R@20被饱和成1.0(假信号); v2扩到48文档后 top20≈42%池子
#   4. v2的54问刻意口语化/同义/泛指, 避开原文关键词; v3新增8问反向补齐——含ASCII精确token(RRF/IDF/
#      InnoDB/BGE-M3等), 这是BM25稀疏路理论上应发挥的查询类型(v2只有改写型查询, hybrid增益无数据的根因)
#   5. v3全部GOLDEN问带answer参考答案: 独立judge(异模型)据此评answer_correct, 替代verifier自评(1.000无信息量)
#   6. UNANSWERABLE 10问: 语料完全不含答案(可含干扰项检索噪声), 期望行为=grade判无关→降级兜底
#      (citations为空+source!=kb_grounded), 评"诚实降级率"——v2无负样本, 拒答路径从未被测过
#   7. v4新增动机: golden v3实测评测出剩余10缺口问的失败签名=文档在候选池但cross-encoder打分<0.30
#      (口语问↔关键词化文档配对是精排薄弱轴), 故v4按3:1:1.3加厚口语改写型/跨簇/关键词型, 使reranker
#      A/B(20261128 Qwen3-Reranker vs bge-v2-m3)在该轴上有足够统计功效

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

# 主题文档id集合(UNANSWERABLE完整性校验/judge口径用)
TOPIC_IDS = frozenset(doc_id for doc_id, _ in DOCUMENTS if doc_id not in DISTRACTOR_IDS)

GOLDEN_VERSION = "v5"

GOLDEN = [
    # --- v1的10问原样保留(基线可对比) ---
    # ponytail: v2的54问刻意口语化/同义/泛指, 避开原文关键词, 考验dense能否被语义近似干扰项带偏
    {"q": "DeepRAG用什么框架构建状态机?", "relevant": ["doc_arch"], "answer": "LangGraph"},
    {"q": "DeepRAG如何对检索结果做精排?", "relevant": ["doc_retrieval"], "answer": "用BGE-Reranker交叉编码器精排"},          # crossencoder干扰
    {"q": "这个系统怎么度量检索和生成的好坏?", "relevant": ["doc_eval"], "answer": "用Golden Dataset做消融实验,指标含Recall@K、MRR、Faithfulness、Citation Accuracy"},
    {"q": "哪个向量库支持原生稀疏和RRF融合?", "relevant": ["doc_vecdb"], "answer": "Qdrant"},            # faiss/pinecone干扰
    {"q": "那个输出1024维的多语言嵌入模型是什么?", "relevant": ["doc_embedding"], "answer": "BGE-M3"},   # gpt干扰
    {"q": "检索增强生成为什么能降低幻觉?", "relevant": ["doc_rag"], "answer": "因为通过检索外部知识库为模型提供事实依据,减少凭参数知识作答的幻觉"},
    {"q": "两路召回的结果是怎么合并的?", "relevant": ["doc_retrieval", "doc_vecdb"], "answer": "通过RRF融合"},
    {"q": "存向量的系统和编码向量的模型怎么协作?", "relevant": ["doc_vecdb", "doc_embedding"], "answer": "BGE-M3编码dense向量,Qdrant负责存储并检索这些向量"},
    {"q": "法国首都的著名地标有哪些?", "relevant": ["doc_paris"], "answer": "埃菲尔铁塔、卢浮宫和凯旋门"},                  # london强干扰
    {"q": "DeepRAG怎样避免模型胡说八道?", "relevant": ["doc_arch", "doc_rag"], "answer": "检索外部知识库支撑回答(RAG),再经CRAG状态机做检索-评估-生成-校验自纠"},     # 口语化
    # --- v2新增(覆盖全部24主题, 每主题至少2问; 多relevant跨簇联动问在末尾) ---
    {"q": "DeepRAG里那个检索-评估-再检索的自纠循环在哪个模块实现?", "relevant": ["doc_arch"], "answer": "LangGraph构建的Agent状态机(CRAG纠正式检索)"},
    {"q": "这个项目稀疏检索的词项权重是数据库侧算的吗?", "relevant": ["doc_vecdb", "doc_retrieval"], "answer": "是,Qdrant原生BM25稀疏向量并在库侧做IDF加权"},  # splade干扰: 学习型稀疏在模型侧
    {"q": "想知道生成的答案是不是都基于检索到的原文,该看哪个指标?", "relevant": ["doc_eval"], "answer": "Faithfulness"},
    {"q": "引用来源对不对得上该怎么评估?", "relevant": ["doc_eval"], "answer": "Citation Accuracy"},
    {"q": "外部知识接进来模型就不容易瞎编了,这思路叫什么?", "relevant": ["doc_rag"], "answer": "RAG(检索增强生成)"},
    {"q": "哪个内存数据库既能当缓存又能做排行榜?", "relevant": ["doc_redis"], "answer": "Redis"},       # memcached干扰
    {"q": "想存哈希、列表这些复杂结构做缓存,选那个只支持字符串的还是支持多结构的?", "relevant": ["doc_redis"], "answer": "选支持多结构的Redis(字符串、哈希、列表、集合等)"},
    {"q": "Python后端的异步后台任务用什么组件跑?", "relevant": ["doc_celery"], "answer": "Celery"},      # kafka干扰
    {"q": "失败任务自动重试和定时调度是谁负责的?", "relevant": ["doc_celery"], "answer": "Celery"},      # kafka明说不管重试
    {"q": "订单库存这种要事务和行级锁的数据放哪类数据库?", "relevant": ["doc_mysql"], "answer": "MySQL(InnoDB引擎)"},  # mongodb干扰
    {"q": "强一致的表结构和灵活的文档存储之间,关系型那条路线用哪个?", "relevant": ["doc_mysql"], "answer": "MySQL"},
    {"q": "用户上传的图片和PDF原文件落在哪个云服务?", "relevant": ["doc_oss"], "answer": "阿里云OSS"},      # s3干扰
    {"q": "哪个对象存储能给前端签发临时的下载链接?", "relevant": ["doc_oss"], "answer": "阿里云OSS(签名URL)"},
    {"q": "排查一次问答全链路调用用什么观测平台?", "relevant": ["doc_langfuse"], "answer": "Langfuse"},    # langsmith干扰
    {"q": "能自托管部署、还管prompt版本的LLM观测工具是哪个?", "relevant": ["doc_langfuse"], "answer": "Langfuse"},
    {"q": "涩谷那个全世界最忙的十字路口在哪个首都?", "relevant": ["doc_tokyo"], "answer": "东京"},     # seoul干扰
    {"q": "日本首都看见的那座标志性铁塔叫什么?", "relevant": ["doc_tokyo"], "answer": "东京塔"},
    {"q": "醋饭顶着生鱼片的那道日本料理是什么?", "relevant": ["doc_sushi"], "answer": "寿司"},         # ramen干扰
    {"q": "金枪鱼和玉子烧是哪道料理江户前流派的代表食材?", "relevant": ["doc_sushi"], "answer": "寿司(江户前寿司)"},
    {"q": "把应用连同依赖打包成镜像隔离跑起来的技术叫什么?", "relevant": ["doc_docker"], "answer": "Docker"},  # k8s干扰
    {"q": "镜像的分层文件系统是哪个技术带来的?", "relevant": ["doc_docker"], "answer": "Docker"},
    {"q": "写接口自动出OpenAPI文档的Python Web框架选谁?", "relevant": ["doc_fastapi"], "answer": "FastAPI"},  # flask干扰
    {"q": "想要原生async的现代Web框架,不选默认同步的那个老框架,该用谁?", "relevant": ["doc_fastapi"], "answer": "FastAPI"},
    {"q": "机器可解析的键值对结构化日志用哪个库?", "relevant": ["doc_structlog"], "answer": "structlog"},   # std logging干扰
    {"q": "中文分词在项目里用的什么库?", "relevant": ["doc_jieba"], "answer": "jieba"},                 # pkuseg干扰
    {"q": "支持自定义词典、动态规划切分的分词器是哪个?", "relevant": ["doc_jieba"], "answer": "jieba"},
    {"q": "西湖十景在哪座城市?", "relevant": ["doc_hangzhou"], "answer": "杭州"},                     # suzhou园林干扰
    {"q": "京杭大运河流经的哪座城市以一片湖闻名?", "relevant": ["doc_hangzhou"], "answer": "杭州(西湖)"},
    {"q": "主打换电站补能的中国车企是哪家?", "relevant": ["doc_nio"], "answer": "蔚来"},               # tesla超充干扰
    {"q": "ET5和ES6是哪个牌子的车?", "relevant": ["doc_nio"], "answer": "蔚来"},
    {"q": "太阳系呆在哪个星系里?", "relevant": ["doc_milkyway"], "answer": "银河系"},                   # andromeda干扰
    {"q": "直径十万光年、装着上千亿颗恒星的棒旋星系是?", "relevant": ["doc_milkyway"], "answer": "银河系"},
    {"q": "吃竹子的中国国宝是谁?", "relevant": ["doc_panda"], "answer": "大熊猫"},                      # koala干扰
    {"q": "大熊猫主要蹲在哪些省的高山竹林里?", "relevant": ["doc_panda"], "answer": "四川、陕西和甘肃"},
    {"q": "紫禁城住过哪两个朝代的皇帝?", "relevant": ["doc_forbidden_city"], "answer": "明清两代"},        # potala宫殿干扰
    {"q": "现存规模最大的古代宫殿建筑群是哪座?", "relevant": ["doc_forbidden_city"], "answer": "故宫(紫禁城)"},
    # --- v2跨簇联动问(多relevant) ---
    {"q": "缓存热点数据的内存库和扛异步任务的队列,项目里分别用的什么?", "relevant": ["doc_redis", "doc_celery"], "answer": "Redis与Celery"},
    {"q": "文件解析完的结构化数据进哪个数据库?分词又靠哪个库?", "relevant": ["doc_mysql", "doc_jieba"], "answer": "MySQL;jieba"},
    {"q": "Web框架、任务队列、缓存三件套分别是什么?", "relevant": ["doc_fastapi", "doc_celery", "doc_redis"], "answer": "FastAPI、Celery、Redis"},
    {"q": "LLM调用链观测平台和结构化日志库分别是哪两个?", "relevant": ["doc_langfuse", "doc_structlog"], "answer": "Langfuse;structlog"},
    {"q": "日本和法国的首都各自最著名的地标是什么?", "relevant": ["doc_tokyo", "doc_paris"], "answer": "东京(涩谷路口/东京塔);巴黎(埃菲尔铁塔等)"},
    {"q": "向量存储、Web框架、观测平台的项目选型分别是?", "relevant": ["doc_vecdb", "doc_fastapi", "doc_langfuse"], "answer": "Qdrant;FastAPI;Langfuse"},
    {"q": "意面配什么酱、寿司饭用什么调的味?", "relevant": ["doc_pasta", "doc_sushi"], "answer": "番茄罗勒酱;醋(醋饭)"},
    {"q": "哪道西餐主食的经典吃法是配番茄罗勒酱?", "relevant": ["doc_pasta"], "answer": "意大利面"},   # pizza番茄酱干扰
    # --- v3新增: 关键词型查询(含ASCII精确token, BM25稀疏路理论上应发挥的查询类型) ---
    {"q": "BGE-M3输出的dense向量是多少维?", "relevant": ["doc_embedding"], "answer": "1024维"},
    {"q": "Qdrant对稀疏向量做哪种统计加权?", "relevant": ["doc_vecdb"], "answer": "IDF加权"},
    {"q": "Redis原生支持哪些数据结构?(至少说出三种)", "relevant": ["doc_redis"], "answer": "字符串、哈希、列表、集合等"},
    {"q": "Celery可以用哪两种消息代理当broker?", "relevant": ["doc_celery"], "answer": "Redis或RabbitMQ"},
    {"q": "MySQL靠哪个存储引擎支持事务与行级锁?", "relevant": ["doc_mysql"], "answer": "InnoDB"},
    {"q": "阿里云OSS给前端做临时下载的机制叫什么?", "relevant": ["doc_oss"], "answer": "签名URL"},
    {"q": "structlog输出的日志是什么形态?", "relevant": ["doc_structlog"], "answer": "键值对(机器可解析)"},
    {"q": "BGE-Reranker属于哪类精排模型架构?", "relevant": ["doc_retrieval"], "answer": "交叉编码器(Cross-Encoder)"},  # crossencoder干扰项同域考验区分
    # --- v4新增: 口语改写型(20问) — 对准精排薄弱轴: 口语问↔关键词化文档配对(cross-encoder打分瓶颈) ---
    {"q": "DeepRAG那套自己查自己纠的循环,分哪几步?", "relevant": ["doc_arch"], "answer": "检索、评估、生成、校验"},
    {"q": "项目检索完拿什么模型给结果重新排座次?", "relevant": ["doc_retrieval"], "answer": "BGE-Reranker(交叉编码器)"},
    {"q": "AI应用里专门放向量、按相似度找数据,项目用的哪个库?", "relevant": ["doc_vecdb"], "answer": "Qdrant"},
    {"q": "那个嵌入模型除了中文,还能处理多少种别的语言?", "relevant": ["doc_embedding"], "answer": "100多种语言"},
    {"q": "靠查外部资料压住模型瞎编,实际效果怎么样?", "relevant": ["doc_rag"], "answer": "降低幻觉,提升事实准确性"},
    {"q": "法国那座铁塔之城,除了铁塔还能逛哪些地方?", "relevant": ["doc_paris"], "answer": "卢浮宫、凯旋门"},
    {"q": "做会话状态这种高频小数据读写,放内存里用什么最合适?", "relevant": ["doc_redis"], "answer": "Redis"},
    {"q": "要保证多笔数据库操作要么全成要么全不算,靠什么能力?", "relevant": ["doc_mysql"], "answer": "事务(InnoDB支持ACID)"},
    {"q": "文件想从浏览器不经过中转服务器直接送到云上,这叫什么能力?", "relevant": ["doc_oss"], "answer": "HTTPS直传"},
    {"q": "想让每一条LLM调用都有迹可循、方便排查问题,用什么平台?", "relevant": ["doc_langfuse"], "answer": "Langfuse(追踪全链路trace)"},
    {"q": "浅草寺所在的城市,还有哪些地标可以逛?", "relevant": ["doc_tokyo"], "answer": "涩谷十字路口、东京塔"},
    {"q": "日本料理里拿醋拌出来的那种饭叫什么?", "relevant": ["doc_sushi"], "answer": "醋饭"},
    {"q": "开发的应用想连同运行环境一起封箱带走,靠什么?", "relevant": ["doc_docker"], "answer": "Docker(镜像打包,容器隔离运行)"},
    {"q": "现在写Python服务,类型标注除了提示还能拿来干什么?", "relevant": ["doc_fastapi"], "answer": "FastAPI基于类型标注构建Web框架"},
    {"q": "标准库logging输出的字符串不好按字段检索,换哪个库?", "relevant": ["doc_structlog"], "answer": "structlog(键值对结构化输出)"},
    {"q": "有一座城市既有闻名的湖,又有运河穿过,还有互联网大厂总部,是哪?", "relevant": ["doc_hangzhou"], "answer": "杭州"},
    {"q": "电车没电不去充电桩,几分钟能换电继续走的是哪家的车?", "relevant": ["doc_nio"], "answer": "蔚来(换电站补能)"},
    {"q": "咱们太阳系所在的星系,里头大概有多少颗恒星?", "relevant": ["doc_milkyway"], "answer": "1000亿到4000亿颗"},
    {"q": "国宝除了吃竹子,还爱待在什么样的环境里?", "relevant": ["doc_panda"], "answer": "高山竹林(四川、陕西、甘肃)"},
    {"q": "去北京旅游,中轴线上那片最大的古建筑群是什么?", "relevant": ["doc_forbidden_city"], "answer": "故宫(紫禁城)"},
    # --- v4新增: 跨簇联动问(8问) ---
    {"q": "向量库、对象存储、关系库,这个项目各选了什么?", "relevant": ["doc_vecdb", "doc_oss", "doc_mysql"], "answer": "Qdrant;阿里云OSS;MySQL"},
    {"q": "分词、结构化日志、异步任务队列三件套分别用什么?", "relevant": ["doc_jieba", "doc_structlog", "doc_celery"], "answer": "jieba;structlog;Celery"},
    {"q": "嵌入模型和精排模型,项目分别用的什么?", "relevant": ["doc_embedding", "doc_retrieval"], "answer": "BGE-M3;BGE-Reranker"},
    {"q": "扛事务和扛缓存,MySQL和Redis怎么分工?", "relevant": ["doc_mysql", "doc_redis"], "answer": "事务靠MySQL(InnoDB);缓存靠Redis"},
    {"q": "容器化方案和大模型观测平台,项目分别选了什么?", "relevant": ["doc_docker", "doc_langfuse"], "answer": "Docker;Langfuse"},
    {"q": "巴黎和伦敦都是欧洲名城,哪座城市有埃菲尔铁塔?", "relevant": ["doc_paris"], "answer": "巴黎"},  # london干扰
    {"q": "分词器和向量数据库,项目里分别选了什么方案?", "relevant": ["doc_jieba", "doc_vecdb"], "answer": "jieba;Qdrant"},
    {"q": "缓存、对象存储、观测平台,这个项目分别用了什么?", "relevant": ["doc_redis", "doc_oss", "doc_langfuse"], "answer": "Redis;阿里云OSS;Langfuse"},
    # --- v4新增: 关键词型(10问, 尾8问含ASCII精确token — 保持'尾部=关键词型'的清单不变式) ---
    {"q": "银河系的直径大约是多少光年?", "relevant": ["doc_milkyway"], "answer": "约10万光年"},
    {"q": "RAG的全称是什么?", "relevant": ["doc_rag"], "answer": "Retrieval-Augmented Generation(检索增强生成)"},
    {"q": "CRAG是什么的缩写?", "relevant": ["doc_arch"], "answer": "纠正式检索增强生成(Corrective RAG)"},
    {"q": "BGE-M3支持多少种语言?", "relevant": ["doc_embedding"], "answer": "100多种"},
    {"q": "蔚来ET5和ES6用什么模式补能?", "relevant": ["doc_nio"], "answer": "换电站"},
    {"q": "Celery的broker可以配RabbitMQ吗?", "relevant": ["doc_celery"], "answer": "可以(Redis或RabbitMQ)"},
    {"q": "InnoDB是哪个数据库的存储引擎?", "relevant": ["doc_mysql"], "answer": "MySQL"},
    {"q": "阿里云OSS的Bucket用来存放什么?", "relevant": ["doc_oss"], "answer": "图片、文档等非结构化文件"},
    {"q": "FastAPI自动生成的接口文档是什么格式?", "relevant": ["doc_fastapi"], "answer": "OpenAPI"},
    {"q": "Langfuse支持哪种部署方式?", "relevant": ["doc_langfuse"], "answer": "自托管部署"},
    # --- v5新增: 多轮问(20问, 带"history"字段) — 架构盲区补齐: 生产/query多为多轮, 而golden v4全单轮,
    # history治理→预改写指代消解→检索→多轮grade链路从未被端到端量化。follow-up全部含指代(它/那座/这个),
    # 答案须可从主题文档推出; 评测时conversation_history注入graph(预改写门控has_history=True必改写) ---
    {"q": "那个精排模型属于什么架构?", "history": [
        {"question": "DeepRAG如何对检索结果做精排?", "answer": "用BGE-Reranker交叉编码器精排"}],
     "relevant": ["doc_retrieval"], "answer": "交叉编码器(Cross-Encoder)"},
    {"q": "它支持多少种语言?", "history": [
        {"question": "那个输出1024维的多语言嵌入模型是什么?", "answer": "BGE-M3"}],
     "relevant": ["doc_embedding"], "answer": "100多种语言"},
    {"q": "它原生支持哪些数据结构?", "history": [
        {"question": "哪个内存数据库既能当缓存又能做排行榜?", "answer": "Redis"}],
     "relevant": ["doc_redis"], "answer": "字符串、哈希、列表、集合等"},
    {"q": "它的broker可以配哪些消息代理?", "history": [
        {"question": "Python后端的异步后台任务用什么组件跑?", "answer": "Celery"}],
     "relevant": ["doc_celery"], "answer": "Redis或RabbitMQ"},
    {"q": "它靠哪个存储引擎支持事务与行级锁?", "history": [
        {"question": "订单库存这种要事务和行级锁的数据放哪类数据库?", "answer": "MySQL(InnoDB)"}],
     "relevant": ["doc_mysql"], "answer": "InnoDB"},
    {"q": "它给前端做临时下载的机制叫什么?", "history": [
        {"question": "用户上传的图片和PDF原文件落在哪个云服务?", "answer": "阿里云OSS"}],
     "relevant": ["doc_oss"], "answer": "签名URL"},
    {"q": "这个平台支持哪种部署方式?", "history": [
        {"question": "排查一次问答全链路调用用什么观测平台?", "answer": "Langfuse"}],
     "relevant": ["doc_langfuse"], "answer": "自托管部署"},
    {"q": "那座城市还有哪些地标?", "history": [
        {"question": "涩谷那个全世界最忙的十字路口在哪个首都?", "answer": "东京"}],
     "relevant": ["doc_tokyo"], "answer": "东京塔、浅草寺"},
    {"q": "这道料理的江户前流派以哪两种食材为代表?", "history": [
        {"question": "醋饭顶着生鱼片的那道日本料理是什么?", "answer": "寿司"}],
     "relevant": ["doc_sushi"], "answer": "金枪鱼和玉子烧"},
    {"q": "它的镜像靠什么文件系统构建?", "history": [
        {"question": "把应用连同依赖打包成镜像隔离跑起来的技术叫什么?", "answer": "Docker"}],
     "relevant": ["doc_docker"], "answer": "分层文件系统"},
    {"q": "它原生支持什么请求处理方式?", "history": [
        {"question": "写接口自动出OpenAPI文档的Python Web框架选谁?", "answer": "FastAPI"}],
     "relevant": ["doc_fastapi"], "answer": "async异步请求处理"},
    {"q": "它的输出形态是什么?", "history": [
        {"question": "机器可解析的键值对结构化日志用哪个库?", "answer": "structlog"}],
     "relevant": ["doc_structlog"], "answer": "键值对(机器可解析)"},
    {"q": "那座城市还有什么穿城而过?", "history": [
        {"question": "西湖十景在哪座城市?", "answer": "杭州"}],
     "relevant": ["doc_hangzhou"], "answer": "京杭大运河"},
    {"q": "它的用户社区叫什么?", "history": [
        {"question": "主打换电站补能的中国车企是哪家?", "answer": "蔚来"}],
     "relevant": ["doc_nio"], "answer": "NIO House"},
    {"q": "这个星系的直径是多少?", "history": [
        {"question": "太阳系呆在哪个星系里?", "answer": "银河系"}],
     "relevant": ["doc_milkyway"], "answer": "约10万光年"},
    {"q": "它主要栖息在哪些省?", "history": [
        {"question": "吃竹子的中国国宝是谁?", "answer": "大熊猫"}],
     "relevant": ["doc_panda"], "answer": "四川、陕西和甘肃"},
    {"q": "这座宫殿建筑群位于哪里?", "history": [
        {"question": "紫禁城住过哪两个朝代的皇帝?", "answer": "明清两代"}],
     "relevant": ["doc_forbidden_city"], "answer": "北京中轴线核心"},
    {"q": "那这个思路的缩写叫什么?", "history": [
        {"question": "检索增强生成为什么能降低幻觉?", "answer": "检索外部知识库减少幻觉"}],
     "relevant": ["doc_rag"], "answer": "RAG(检索增强生成)"},
    {"q": "它对稀疏向量做哪种统计加权?", "history": [
        {"question": "哪个向量库支持原生稀疏和RRF融合?", "answer": "Qdrant"}],
     "relevant": ["doc_vecdb"], "answer": "IDF加权"},
    {"q": "那寿司饭用什么调味?", "history": [
        {"question": "哪道西餐主食的经典吃法是配番茄罗勒酱?", "answer": "意大利面"}],
     "relevant": ["doc_sushi"], "answer": "醋(醋饭)"},  # 话题切换型: 前轮意面后轮寿司
]

# v3新增: 不可答负样本(语料完全不含答案; 部分含干扰项检索噪声如tesla/gpt/k8s)
# 期望行为: grade判全部无关 → 降级tool_agent兜底 → citations为空 且 source != kb_grounded(诚实降级)
# 评"诚实降级率"(objective): fabrication = 编造了kb_grounded引用或citation指向语料
UNANSWERABLE = [
    "DeepRAG的作者是谁?",
    "公司年假有几天?",
    "DeepRAG提供Go语言的SDK吗?",
    "特斯拉Model Y的续航是多少公里?",
    "怎么申请GitHub组织的管理员权限?",
    "OpenAI的API定价是每token多少钱?",
    "公司食堂中午一般有什么菜?",
    "pgvector和Qdrant相比哪个更适合生产?",
    "DeepRAG当前的最新版本号是多少?",
    "Kubernetes的Node亲和性规则怎么配置?",
    # --- v4新增(8问, 部分含真实世界陷阱: 语料只描述单边事实, 不含比较/价格/供应商等答案) ---
    "DeepRAG支持私有化部署吗?报价多少?",
    "阿里云OSS和腾讯COS哪个更便宜?",
    "Qwen3-Reranker和BGE-Reranker哪个效果更好?",
    "蔚来的电池是哪家供应商提供的?",
    "故宫门票一张多少钱?",
    "寿司和拉面哪个热量更高?",
    "MySQL 9.0有什么新特性?",
    "部署DeepRAG需要多少GPU显存?",
]
