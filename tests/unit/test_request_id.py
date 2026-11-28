# 架构优化④: request_id贯穿单测 — contextvars绑定 + structlog processor注入
from src.core.logging import _inject_request_id, new_request_id, request_id_var


def test_new_request_id_binds_contextvar():
    rid = new_request_id()
    assert rid, "request_id非空"
    assert request_id_var.get() == rid
    rid2 = new_request_id()
    assert rid2 != rid, "每次请求新id"
    assert request_id_var.get() == rid2


def test_processor_injects_request_id():
    new_request_id()
    event = _inject_request_id(None, None, {"event": "trace"})
    assert event["request_id"] == request_id_var.get()


def test_processor_omits_when_unbound():
    # 模拟无请求上下文(评测/脚本直调): default空串 → 不加字段(向后兼容)
    token = request_id_var.set("")
    try:
        event = _inject_request_id(None, None, {"event": "trace"})
        assert "request_id" not in event
    finally:
        request_id_var.reset(token)
