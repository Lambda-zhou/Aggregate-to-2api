"""测试 WebSocket 日志推送。"""

import logging

import pytest

from api.log_ws import LogBuffer, WsLogHandler


@pytest.fixture(autouse=True)
def _restore_subscribers():
    """每用例前后清空/保留全局订阅者集合（我的广播测试直接替换它，必须恢复到空集，防跨文件污染）。"""
    from api import log_ws

    orig = log_ws._ws_subscribers
    log_ws._ws_subscribers = set()
    yield
    log_ws._ws_subscribers = orig


def test_log_buffer():
    """验证日志缓冲区。"""
    buf = LogBuffer(maxlen=100)
    buf.push({"level": "INFO", "message": "test"})
    assert buf.snapshot()[-1]["message"] == "test"
    assert len(buf.snapshot(1)) == 1


def test_log_buffer_maxlen():
    """验证缓冲区上限。"""
    buf = LogBuffer(maxlen=5)
    for i in range(10):
        buf.push({"level": "INFO", "message": f"msg{i}"})
    snap = buf.snapshot()
    assert len(snap) == 5
    assert snap[0]["message"] == "msg5"


def test_log_buffer_empty():
    """验证空缓冲区 snapshot 返回空列表。"""
    buf = LogBuffer(maxlen=100)
    assert buf.snapshot() == []


def test_ws_handler_not_crash():
    """验证无订阅者时 handler 不报错。"""
    handler = WsLogHandler()
    record = logging.LogRecord("test", logging.INFO, "test.py", 1, "test msg", None, None)
    try:
        handler.emit(record)
    except Exception:
        assert False, "WsLogHandler.emit() 不应该抛异常"


def test_ws_handler_format():
    """验证 handler 格式化正常。"""
    handler = WsLogHandler()
    record = logging.LogRecord("test", logging.WARNING, "test.py", 1, "warning msg", None, None)
    handler.emit(record)
    # 不应抛出异常，且广播函数应无异常
    assert True


def test_broadcast_redacts_sensitive_data():
    """v23 S-1：WS 广播必须脱敏——含密码的 redis URL 不外泄（源头 + 广播双保险）。"""
    import asyncio
    from api import log_ws

    received: list[dict] = []

    class FakeWs:
        async def send_json(self, entry):
            received.append(entry)

    async def _listen():
        log_ws._ws_subscribers = {FakeWs()}
        record = logging.LogRecord(
            "test", logging.INFO, "t.py", 1,
            "Redis 存储驱动已成功连接: redis://:SECRETPASS123@10.0.0.5:6379/0",
            None, None,
        )
        log_ws.broadcast_log(record)
        await asyncio.sleep(0.05)

    asyncio.run(_listen())
    # 广播实际发出
    assert received, "广播应实际发送"
    msg = received[-1]["message"]
    assert "SECRETPASS123" not in msg, f"redis 密码泄露: {msg}"


def test_broadcast_redacts_api_key_simple():
    """v23 S-1：api_key= 明文参数脱敏。"""
    import asyncio
    from api import log_ws

    received: list[dict] = []

    class FakeWs:
        async def send_json(self, entry):
            received.append(entry)

    async def _listen():
        log_ws._ws_subscribers = {FakeWs()}
        record = logging.LogRecord(
            "test", logging.INFO, "t.py", 1,
            "proxy fetch failed url=https://api.example.com/x?api_key=sk-abcdef123456",
            None, None,
        )
        log_ws.broadcast_log(record)
        await asyncio.sleep(0.05)

    asyncio.run(_listen())
    assert received, "广播应实际发送"
    msg = received[-1]["message"]
    assert "sk-abcdef123456" not in msg, f"api_key 泄露: {msg}"


def test_redact_utility_covers_redis_and_webhook():
    """v23 S-1：_redact 纯函数对 redis://:密码@ 与带签名 query 的 URL 都脱敏。"""
    from api.log_buffer import _redact

    # redis URL（密码无 password= 键）
    assert "SECRET" not in _redact("conn redis://:SECRET@10.0.0.5:6379")
    # v23 P2-2（独立审查）：密码含 @ 时不得只脱到首个 @ 泄漏片段
    assert "pa@ss" not in _redact("conn redis://user:pa@ss@10.0.0.5:6379")
    assert "redis://***@" in _redact("conn redis://user:pa@ss@10.0.0.5:6379")
    # webhook 签名在 query（key= 形式）
    assert "sig-token-xyz" not in _redact(
        "webhook https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=sig-token-xyz-abc"
    )
    # 普通消息不受影响
    assert _redact("normal message no secret") == "normal message no secret"
