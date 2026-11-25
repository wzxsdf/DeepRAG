# 集成验证(P0.1 缓存版本失效): 首问写缓存 → 复问命中 → 数据变更(删除/入库) → generation递增 → 旧缓存不复用
# 依赖: API(localhost:8000) + MySQL + Redis + Qdrant + OSS + LLM/embedding 全部可用
# 证据: Redis query_cache_epoch:{tenant} 世代递增 + 精确键存在性 + 同问题新旧答案事实值(777→888)
# 已知设计: 失效粒度为租户级 generation bump(入库成功/文档删除钩子), 非kb级
import sys, os, io, time, json, uuid, hashlib, httpx
from dotenv import load_dotenv
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

load_dotenv()
BASE = os.getenv("API_BASE", "http://localhost:8000")
TENANT = "default"
MODE = "kb"  # 显式kb模式, 走限定kb_ids检索

import redis as redis_lib


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


def make_marker_pdf(path, marker, value):
    """生成内容可区分的测试PDF(运行时动态构造, 每次运行marker唯一→问题必为首次问题).
    注意: kb模式证据喂给LLM时截断前150字符(tools.py), 事实值必须落在文本前段."""
    import fitz  # PyMuPDF, 与worker解析器同源
    doc = fitz.open()
    page = doc.new_page()
    text = (f"Zephyrite-{marker} melting point is {value} degrees Celsius. "
            f"Source: DeepRAG corpus fact sheet {marker}, catalogued for the cache invalidation verification.")
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
QUESTION = f"Zephyrite-{marker} 的熔点是多少摄氏度?"
TMP_A, TMP_B = f"_cache_test_A_{marker}.pdf", f"_cache_test_B_{marker}.pdf"
g0 = epoch()
print(f"[0] tenant={TENANT} 初始generation={g0}")
print(f"    测试问题: {QUESTION}")

kb_id = None
kb_deleted = False
try:
    kb_id = httpx.post(f"{BASE}/api/v1/kbs", json={"name": f"缓存验证_{marker}"}, headers=H).json()["id"]
    print(f"[1] KB: {kb_id}")

    # [2] 上传docA(事实值777) → 入库成功钩子应bump generation
    make_marker_pdf(TMP_A, marker, 777)
    doc_a = upload_doc(kb_id, TMP_A, f"cache_A_{marker}.pdf")
    assert wait_indexed(doc_a) == "INDEXED", "docA入库失败"
    g1 = poll_epoch(g0, 60, "docA入库成功")
    print(f"[2] docA={doc_a[:8]} 已入库(INDEXED)")

    # [3] 首问 → 应写当前世代精确缓存
    r1 = ask(QUESTION, kb_id)
    assert "777" in r1["answer"], f"首问答案未含事实值777: {r1['answer'][:200]}"
    assert any(c["document_id"] == doc_a for c in r1["citations"]), f"首问引用未指向docA: {r1['citations']}"
    key1, p1 = poll_cache(QUESTION, kb_id, g1)
    assert p1, f"首问后未写入generation={g1}的精确缓存键 {key1}"
    assert p1["result"]["answer"] == r1["answer"], "缓存内容与响应不一致"
    print(f"[3] 首问answer含777, 引用docA; 缓存已写入 {key1[:40]}...")

    # [4] 复问 → 命中缓存(逐字节一致; LLM重新生成不可能逐字相同, 一致即命中证据)
    r2 = ask(QUESTION, kb_id)
    assert r2["answer"] == p1["result"]["answer"], "复问答案与缓存不一致, 疑似未命中"
    assert r2["citations"] == p1["result"]["citations"], "复问引用与缓存不一致, 疑似未命中"
    print("[4] 复问命中缓存: answer/citations与缓存逐字一致")

    # [5] 删除docA → 删除钩子bump(删除前失效), 旧世代缓存仍在Redis但generation不匹配不可命中
    del_r = httpx.delete(f"{BASE}/api/v1/documents/{doc_a}", headers=H)
    assert del_r.status_code == 200, f"删除docA失败: {del_r.status_code}"
    g2 = poll_epoch(g1, 30, "删除docA")
    key_old, p_old = cached_payload(QUESTION, kb_id, g1)
    assert p_old is not None, "旧世代缓存条目应仍在Redis(TTL内, 供比对)"
    print(f"[5] docA已删除, generation {g1}->{g2}; 旧世代条目仍在但不可命中(generation校验拦截)")

    # [6] 上传docB(同marker事实值改为888) → 再次bump
    make_marker_pdf(TMP_B, marker, 888)
    doc_b = upload_doc(kb_id, TMP_B, f"cache_B_{marker}.pdf")
    assert wait_indexed(doc_b) == "INDEXED", "docB入库失败"
    g3 = poll_epoch(g2, 60, "docB入库成功")
    print(f"[6] docB={doc_b[:8]} 已入库(INDEXED)")

    # [7] 完全相同问题+scope重查 → 旧缓存答案/引用不得复用
    r3 = ask(QUESTION, kb_id)
    assert "777" not in r3["answer"], f"重查复用了旧缓存答案(含777): {r3['answer'][:200]}"
    assert "888" in r3["answer"], f"重查未返回新事实值888: {r3['answer'][:200]}"
    cite_ids = [c["document_id"] for c in r3["citations"]]
    assert doc_a not in cite_ids, f"重查引用仍指向已删除docA: {cite_ids}"
    assert doc_b in cite_ids, f"重查引用未指向docB: {cite_ids}"
    key3, p3 = poll_cache(QUESTION, kb_id, g3)
    assert p3 and "888" in p3["result"]["answer"], "重查后未写入新世代缓存"
    print(f"[7] 重查返回新事实888, 引用切换docB; 新世代缓存已写入 generation={g3}")

    # [8] 清理本次数据(删KB, 全清)
    assert httpx.delete(f"{BASE}/api/v1/kbs/{kb_id}", headers=H).status_code == 200
    kb_deleted = True
    g4 = poll_epoch(g3, 30, "删除KB")
    print(f"[8] KB已删除, generation {g3}->{g4}")
    print("\n[OK] 缓存失效链路验证通过: 写入 -> 命中 -> 删除/入库bump -> 旧缓存不复用(答案777->888, 引用A->B)")
    print(f"     失效粒度: 租户级 generation (default: g0={g0} -> g4={g4}, 共4次bump: 入库A/删A/入库B/删KB)")
finally:
    for tmp in (TMP_A, TMP_B):
        if os.path.exists(tmp):
            os.remove(tmp)
    if kb_id and not kb_deleted:  # 失败路径兜底清理, 不留孤儿测试数据(purge含Qdrant/OSS/MySQL, 放宽超时)
        try:
            httpx.delete(f"{BASE}/api/v1/kbs/{kb_id}", headers=H, timeout=60)
            print(f"[finally] 异常退出, 已兜底删除测试KB {kb_id}")
        except Exception as e:
            print(f"[finally] 测试KB {kb_id} 清理失败: {e}, 请手动删除")
