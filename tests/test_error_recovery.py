"""
ErrorRecovery 单元测试
"""
from __future__ import annotations
import pytest
from core.error_recovery import ErrorRecovery


@pytest.fixture
def recovery():
    return ErrorRecovery()


class TestErrorClassification:
    """错误分类测试"""

    def test_rate_limit(self, recovery):
        assert recovery.classify_error("rate_limit exceeded") == "rate_limit"
        assert recovery.classify_error("too many requests") == "rate_limit"
        assert recovery.classify_error("429 Too Many Requests") == "rate_limit"

    def test_quota_exceeded(self, recovery):
        assert recovery.classify_error("insufficient_quota") == "quota_exceeded"
        assert recovery.classify_error("credit limit exceeded") == "quota_exceeded"

    def test_context_too_long(self, recovery):
        assert recovery.classify_error("context_length_too_long") == "context_too_long"
        assert recovery.classify_error("context is too long") == "context_too_long"
        assert recovery.classify_error("maximum context length") == "context_too_long"

    def test_auth_error(self, recovery):
        assert recovery.classify_error("authentication failed") == "auth_error"
        assert recovery.classify_error("API key authentication failed") == "auth_error"
        assert recovery.classify_error("401 Unauthorized") == "auth_error"

    def test_model_not_found(self, recovery):
        assert recovery.classify_error("model not found") == "model_not_found"
        assert recovery.classify_error("not support gpt-5") == "model_not_found"

    def test_network_error(self, recovery):
        assert recovery.classify_error("connection timeout") == "network_error"
        assert recovery.classify_error("econnrefused") == "network_error"

    def test_invalid_output(self, recovery):
        assert recovery.classify_error("invalid json output") == "invalid_output"
        assert recovery.classify_error("parse error") == "invalid_output"

    def test_unknown_error(self, recovery):
        assert recovery.classify_error("something completely unexpected") == "unknown"


class TestRecoveryStrategies:
    """恢复策略测试"""

    def test_rate_limit_strategy(self, recovery):
        assert recovery.get_strategy("rate_limit") == "backoff_retry"

    def test_auth_error_unrecoverable(self, recovery):
        assert recovery.is_recoverable("auth_error") is False

    def test_network_recoverable(self, recovery):
        assert recovery.is_recoverable("network_error") is True

    def test_max_retries_count(self, recovery):
        assert recovery.get_max_retries("rate_limit") == 5
        assert recovery.get_max_retries("auth_error") == 0
        assert recovery.get_max_retries("unknown") == 2


@pytest.mark.asyncio
async def test_watchdog_timeout():
    """测试 Watchdog 超时"""
    from core.error_recovery import Watchdog
    triggered = []

    async def on_timeout(task_id: str):
        triggered.append(task_id)

    w = Watchdog(timeout=0.1)
    w.start("TEST", on_timeout)
    await __import__("asyncio").sleep(0.2)
    assert "TEST" in triggered
    w.cancel()


@pytest.mark.asyncio
async def test_watchdog_cancel():
    """测试 Watchdog 取消"""
    from core.error_recovery import Watchdog
    triggered = []

    async def on_timeout(task_id: str):
        triggered.append(task_id)

    w = Watchdog(timeout=10.0)
    w.start("TEST", on_timeout)
    w.cancel()
    assert len(triggered) == 0