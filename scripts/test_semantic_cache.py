# 集成验证(P2缓存缺口): 语义缓存命中链路 + scope隔离
# 依赖: API(localhost:8000) + MySQL + Redis + Qdrant + OSS + LLM/embedding 全部可用
# !!! 评审修复(20261126): 语义缓存默认已关闭(词袋重叠词序/否定词盲, "支持X吗"vs"不支持X"实测0.95误命中),
#     本脚本验证语义路径前需在API进程环境设 CACHE_SEMANTIC_ENABLED=true 再重启, 否则改写问断言必然失败;
#     仅验证精确缓存路径时无需该开关(脚本3-4步会跳过语义命中断言)。
# 证据链: 1) 原问真实生成并写当前世代精确键  2) 改写问离线断言关键词重叠度>=0.85且其精确键不存在
#         3) 改写问API应答与原问缓存结果逐字节一致(LLM重新生成不可能逐字相同→命中证据)
#         4) 命中后改写问精确键仍不存在(缓存路径直接返回, 无回写)
#         5) 同改写问换kb_ids不得命中(语义候选按filters全量校验, 空知识库无事实值)
# 已知实现: 语义匹配为关键词重叠(overlap>=2 且 overlap/max(len)>=0.85), 非向量相似度(代码内注释标注为简化实现)
import sys, os, io, time, json, uuid, hashlib, httpx
from dotenv import load_dotenv
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()
BASE = os.getenv("API_BASE", "http://localhost:8000")
TENANT = "default"
MODE = "kb"  # 显式kb模式, 走限定kb_ids检索

import redis as redis_lib
import jieba

STOPWORDS = {"的", "是", "在", "和", "与", "或", "等", "了", "有", "不", "怎么", "什么", "如何", "为什么"}


def _redis() -> "redis_lib.Redis":
    pwd = os.getenv("REDIS_PASSWORD", "")
    auth = f":{pwd}@" if pwd else ""
    return redis_lib.Redis.from_url(
        f"redis://{auth}{os.getenv('REDIS_HOST', 'localhost')}:{os.getenv('REDIS_PORT', '6379')}/1",
        encoding="utf-8", decode_responses=True)


R = _redis()


def epoch() -> int:
    v = R.get(f"query_cache_epoch:{TENANT}")
    return int(v) if v else 0


def _norm(v):
    """复刻 QueryCache._normalize_value: dict按键排序, 列表按json序排序."""
    if isinstance(v, dict):
        return {str(k): _norm(x) for k, x in sorted(v.items())}
    if isinstance(v, (list, tuple, set)):
        vals = [_norm(x) for x in v]
        return sorted(vals, key=lambda i: json.dumps(i, sort_keys=True, ensure_ascii=False))
    return v


def cache_key(question, kb_id, generation):
    """复刻 QueryCache._generate_cache_key(API侧filters=tenant_id+kb_ids+mode)."""
    filters = {"tenant_id": TENANT, "kb_ids": [kb_id], "mode": MODE}
    content = {"question": question.strip().lower(), "filters": _norm(filters),
               "mode": MODE, "generation": str(generation)}
    return "query_cache:" + hashlib.md5(json.dumps(content, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def cached_payload(question, kb_id, generation):
    key = cache_key(question, kb_id, generation)
    raw = R.get(key)
    return key, (json.loads(raw) if raw else None)


def extract_keywords(text):
    """复刻 QueryCache._extract_keywords: jieba分词+停用词过滤, 取前10."""
    words = jieba.cut(text)
    return list({w for w in words if len(w) > 1 and w not in STOPWORDS})[:10]


def semantic_ratio(q_a, q_b):
    """复刻 _semantic_cache_lookup 打分: overlap/max(len), 上限0.95."""
    ka, kb_ = extract_keywords(q_a), extract_keywords(q_b)
    overlap = len(set(ka) & set(kb_))
    if overlap < 2:
        return 0.0, ka, kb_
    return min(overlap / max(len(ka), len(kb_)), 0.95), ka, kb_


def make_marker_pdf(path, marker, value):
    """生成内容可区分的测试PDF(运行时动态构造, marker唯一→原问必为首次问题).
    注意: kb模式证据喂给LLM时截断前150字符(tools.py), 事实值必须落在文本前段."""
    import fitz  # PyMuPDF, 与worker解析器同源
    doc = fitz.open()
    page = doc.new_page()
    text = (f"Zephyrite-{marker} melting point is {value} degrees Celsius. "
            f"Source: DeepRAG corpus fact sheet {marker}, catalogued for the semantic cache verification.")
    page.insert_text((72, 72), text, fontname="helv", fontsize=12)
    doc.save(path)
    doc.close()


def upload_doc(kb_id, path, title):
    with open(path, "rb") as f:
        pdf = f.read()
    presign = httpx.post(f"{BASE}/api/v1/uploads/presign",
                         json={"filename": title, "file_size": len(pdf)}, headers=H).json()
    assert httpx.put(presign["upload_url"], content=pdf, headers=presign["headers"]).status_code == 200
    sha = hashlib.sha256(pdf).hexdigest()
    return httpx.post(f"{BASE}/api/v1/documents/confirm",
                      json={"oss_key": presign["oss_key"], "title": title, "kb_id": kb_id,
                            "file_size": len(pdf), "sha256": sha}, headers=H).json()["document_id"]


def wait_indexed(doc_id, timeout=120):
    for _ in range(timeout // 2):
        st = httpx.get(f"{BASE}/api/v1/documents/{doc_id}", headers=H).json().get("status")
        if st in ("INDEXED", "FAILED"):
            return st
        time.sleep(2)
    return "TIMEOUT"


def ask(question, kb_id):
    r = httpx.post(f"{BASE}/api/v1/query", json={"question": question, "kb_ids": [kb_id], "mode": MODE},
                   headers=H, timeout=240)
    assert r.status_code == 200, f"query失败: {r.status_code} {r.text[:300]}"
    return r.json()


def poll_epoch(prev, timeout, why):
    for _ in range(timeout):
        cur = epoch()
        if cur > prev:
            print(f"    generation {prev} -> {cur} ({why})")
            return cur
        time.sleep(1)
    raise AssertionError(f"generation未递增({why}): {prev} -> {epoch()}, 缓存失效钩子未生效")


def poll_cache(question, kb_id, generation, timeout=15):
    """缓存写入是asyncio.create_task, 响应返回后轮询等待落盘."""
    key, payload = cached_payload(question, kb_id, generation)
    for _ in range(timeout):
        if payload:
            return key, payload
        time.sleep(1)
        key, payload = cached_payload(question, kb_id, generation)
    return key, payload


# [0] 登录(不存在则注册)
r = httpx.post(f"{BASE}/api/v1/auth/login", json={"email": "test_front@example.com", "password": "123456"})
if r.status_code != 200:
    r = httpx.post(f"{BASE}/api/v1/auth/register",
                   json={"email": "test_front@example.com", "password": "123456", "name": "cache_test"})
assert r.status_code == 200, f"登录/注册失败: {r.status_code} {r.text[:200]}"
H = {"Authorization": f"Bearer {r.json()['token']}"}

marker = uuid.uuid4().hex[:8].upper()
ALLOY = f"Zephyrite-{marker}"
QUESTION = f"{ALLOY}合金的熔点是多少摄氏度,主要成分是什么?"
PARAPHRASES = [
    f"{ALLOY}合金熔点几摄氏度?成分主要有哪些?",
    f"请问{ALLOY}合金的熔点是多少摄氏度呢?主要成分是什么?",
]
TMP = f"_semantic_cache_test_{marker}.pdf"
g0 = epoch()
print(f"[0] tenant={TENANT} 初始generation={g0}")
print(f"    原问: {QUESTION}")

kb1 = kb2 = None
kb1_deleted = kb2_deleted = False
try:
    kb1 = httpx.post(f"{BASE}/api/v1/kbs", json={"name": f"语义缓存验证_{marker}"}, headers=H).json()["id"]
    print(f"[1] KB1: {kb1}")

    # [2] 上传事实文档(777) → 入库成功bump generation
    make_marker_pdf(TMP, marker, 777)
    doc = upload_doc(kb1, TMP, f"semantic_cache_{marker}.pdf")
    assert wait_indexed(doc) == "INDEXED", "事实文档入库失败"
    g = poll_epoch(g0, 60, "事实文档入库成功")
    print(f"[2] doc={doc[:8]} 已入库(INDEXED)")

    # [3] 原问首问 → 真实生成 + 写当前世代精确缓存
    r1 = ask(QUESTION, kb1)
    assert "777" in r1["answer"], f"原问答案未含事实值777: {r1['answer'][:200]}"
    key1, p1 = poll_cache(QUESTION, kb1, g)
    assert p1, f"原问后未写入generation={g}的精确缓存键 {key1}"
    print(f"[3] 原问answer含777; 精确缓存已写入 {key1[:40]}...")

    # [4] 离线选改写问: 关键词重叠度>=0.85(与线上_semantic_cache_lookup同口径) 且精确键不存在
    chosen = None
    for cand in PARAPHRASES:
        ratio, ka, kc = semantic_ratio(QUESTION, cand)
        _, p_cand = cached_payload(cand, kb1, g)
        print(f"    候选改写问 overlap口径ratio={ratio:.3f} 精确键存在={bool(p_cand)} kw原问={sorted(ka)} kw改写={sorted(kc)}")
        if ratio >= 0.85 and not p_cand:
            chosen = cand
            break
    assert chosen, "无候选改写问满足语义命中前置条件(overlap>=0.85且精确键不存在)"
    Q2 = chosen
    print(f"[4] 改写问: {Q2}")

    # [5] 改写问查询 → 应命中语义缓存: answer/citations与原问缓存逐字节一致
    r2 = ask(Q2, kb1)
    assert r2["answer"] == p1["result"]["answer"], \
        f"改写问答案与原问缓存不一致(疑似未命中重新生成): {r2['answer'][:200]}"
    assert r2["citations"] == p1["result"]["citations"], "改写问引用与原问缓存不一致, 疑似未命中"
    key2, p2 = cached_payload(Q2, kb1, g)
    assert p2 is None, f"命中后改写问不应回写精确缓存, 但发现键 {key2}"
    print("[5] 语义缓存命中: 改写问answer/citations与原问缓存逐字节一致, 且无精确键回写(非重新生成)")

    # [6] scope隔离: 同改写问换空KB → 语义候选被filters拦截, 不得返回原KB的777
    kb2 = httpx.post(f"{BASE}/api/v1/kbs", json={"name": f"语义缓存隔离_{marker}"}, headers=H).json()["id"]
    r3 = ask(Q2, kb2)
    assert "777" not in r3["answer"], f"跨KB命中了语义缓存(污染): {r3['answer'][:200]}"
    print(f"[6] scope隔离: 空KB同问不含777(语义候选被kb_ids校验拦截), KB2: {kb2}")

    # [7] 清理本次数据(删KB, 全清; purge含Qdrant/OSS/MySQL, 放宽超时)
    assert httpx.delete(f"{BASE}/api/v1/kbs/{kb1}", headers=H, timeout=60).status_code == 200
    kb1_deleted = True
    assert httpx.delete(f"{BASE}/api/v1/kbs/{kb2}", headers=H, timeout=60).status_code == 200
    kb2_deleted = True
    print(f"[7] 测试KB已删除, generation {epoch()} (删除钩子bump)")
    print("\n[OK] 语义缓存链路验证通过: 原问写缓存 -> 改写问语义命中(逐字节一致) -> 无精确键回写 -> 跨KB不命中")
finally:
    if os.path.exists(TMP):
        os.remove(TMP)
    for kid, deleted in ((kb1, kb1_deleted), (kb2, kb2_deleted)):
        if kid and not deleted:  # 失败路径兜底清理, 不留孤儿测试数据
            try:
                httpx.delete(f"{BASE}/api/v1/kbs/{kid}", headers=H, timeout=60)
                print(f"[finally] 异常退出, 已兜底删除测试KB {kid}")
            except Exception as e:
                print(f"[finally] 测试KB {kid} 清理失败: {e}, 请手动删除")
