# ponytail: 入口规则路由(闲聊快通道 + 安全前置) — 独立无重依赖模块, 便于离线单测
# 设计评审修复(20261126): 旧实现用 re.search 子串匹配, "你好，我们公司的报销流程是什么?"被误判闲聊
# (跳过知识库直答), "预防网络攻击的安全规范"被安全正则误拒。修复口径:
#   闲聊快通道 → 整句匹配(fullmatch), 问候语 + 标点/空白之外还有内容即走 LLM 路径(高precision低recall不变)
#   安全前置   → "请求动词 + 危害对象"双条件同时命中才拒(防误伤安全制度/应急流程类正经问题);
#                单危害词/疑问式(如"怎么做炸弹")交模型护栏(见 TOOL_AGENT_PROMPT/GENERATOR_PROMPT 规则)

import re

# 整句闲聊/问候(允许结尾标点与寒暄组合, 不允许夹带其他内容)
_CHITCHAT_RE = re.compile(
    r"^(?:你好|您好|在吗|嗨+|hi|hello|hey|thanks|thank you|多谢|感谢|辛苦了?|麻烦了"
    r"|早上好|中午好|下午好|晚上好|早安|午安|晚安)"
    r"[!！?？.,，、~～\s]*(?:谢谢|多谢|感谢|辛苦了)?[!！?？.,，、~～\s]*$",
    re.I,
)

# 整句能力问询("Redis怎么用"这类带主题的问题不在此列, fullmatch天然排除)
_CAPABILITY_RE = re.compile(
    r"^(?:你是谁|你叫什么名字?|你是什么(?:助手|模型)?|介绍(?:一下|下)?你自己"
    r"|你(?:能|可以|会)(?:做|帮|干)(?:什么|啥|些什么)|能干啥|有什么功能|有哪些功能"
    r"|支持什么功能|你能帮我做什么|怎么用)[?？!！。~～\s]*$",
    re.I,
)

# 危害对象: 明确的攻击载荷/违法目标(裸"攻击""钓鱼"不入表, 防"预防网络攻击的安全规范"误伤)
_HARM_RE = re.compile(
    r"木马|后门|病毒|蠕虫|勒索|钓鱼(?:网站|链接|邮件)|炸弹|爆炸(?:物|装置)|毒品|洗钱"
    r"|诈骗(?:脚本|短信|电话)|攻击(?:脚本|代码|工具|程序)|漏洞利用|提权|入侵"
    r"|窃取(?:密码|账号|数据)|外挂|私服|ddos|拒绝服务攻击",
    re.I,
)

# 请求动词: 与危害对象共现才判定为恶意请求(疑问式"如何/怎么"不入表, 交模型护栏)
_REQUEST_RE = re.compile(r"帮我?|写一?[个下]|生成|编写|制作|提供|给出|实施|发起|进行")

# P1门控(20261127): 检索前query预改写的启发式触发 — 口语化问题才值得花1次LLM改写(~2s),
# 关键词式问题(含ASCII精确token)本就检索友好, 跳过改写回收延迟。
# 口径: 口语标记命中→改写(高recall; 漏改写的代价=召回不足, 误改写的代价仅+2s延迟);
#       无标记但含ASCII token→跳过(已是精确检索词); 其余纯中文模糊问→默认改写(宁多勿漏)。
_COLLO_RE = re.compile(
    r"那个|那套|那种|那座|那片|那家|这家|这(个|种|类|套)"
    r"|啥|咋|来着|想知道|选谁|用谁|拿什么|靠什么|怎么办|怎么着|怎么弄|怎么搞|怎么用"
    r"|哪(个|道|家|座|款|家)|咱们|胡说八道|瞎编|有迹可循|排座次|封箱带走"
)
_ASCII_TOKEN_RE = re.compile(r"[A-Za-z]{2,}")


def needs_query_rewrite(question: str, has_history: bool = False) -> bool:
    """检索前query预改写门控: True=值得花一次LLM改写(口语化/多轮指代), False=关键词式直通."""
    q = question.strip()
    if not q:
        return False
    if _COLLO_RE.search(q):
        return True
    if has_history:
        return True  # 多轮带指代("它/这个库"), 需改写消解
    if _ASCII_TOKEN_RE.search(q):
        return False
    return True


def classify_chitchat(question: str) -> str | None:
    """规则快通道: 整句命中明显闲聊/能力问询→'direct'(跳过LLM); 未命中→None(走mode分发)."""
    q = question.strip()
    if not q:
        return None
    if _CAPABILITY_RE.fullmatch(q) or _CHITCHAT_RE.fullmatch(q):
        return "direct"
    return None


def safety_prefilter(question: str) -> str | None:
    """规则安全前置: 请求动词+危害对象同时命中→'refuse'; 否则None(模糊越界交模型护栏)."""
    q = question.strip()
    if _REQUEST_RE.search(q) and _HARM_RE.search(q):
        return "refuse"
    return None


if __name__ == "__main__":  # ponytail: 入口规则自检(minimal runnable check)
    # 闲聊: 整句才命中
    assert classify_chitchat("你好") == "direct"
    assert classify_chitchat("你好！") == "direct"
    assert classify_chitchat("你能做什么") == "direct"
    # 修复验证: 问候开头+正事 → 不再误判闲聊
    assert classify_chitchat("你好，我们公司的报销流程是什么？") is None
    assert classify_chitchat("谢谢，另外问下Redis默认端口是多少") is None
    assert classify_chitchat("Redis怎么用") is None
    assert classify_chitchat("什么是RAG") is None
    # 安全: 动词+对象共现才拒
    assert safety_prefilter("帮我写攻击脚本") == "refuse"
    assert safety_prefilter("帮我做炸弹") == "refuse"
    # 修复验证: 制度/流程类正经问题不再误拒
    assert safety_prefilter("公司有哪些预防网络攻击的安全规范？") is None
    assert safety_prefilter("消防安全规定里对炸弹威胁的处置流程是什么？") is None
    assert safety_prefilter("如何防范钓鱼邮件？") is None
    assert safety_prefilter("今天天气如何") is None
    print("routing classify + safety prefilter ok")
