"""
HealthMonitor - 健康监控

需求覆盖（第 8 章）：
- API 可用性检测
- 内存监控
- 错误率统计
- 自动告警
"""
from __future__ import annotations
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ProviderHealth:
    name: str
    is_available: bool = True
    last_check: float = 0.0
    consecutive_failures: int = 0
    total_requests: int = 0
    failed_requests: int = 0
    avg_latency_ms: float = 0.0


class HealthMonitor:
    """健康监控器"""

    def __init__(self, check_interval: int = 30, failure_threshold: int = 3):
        self.check_interval = check_interval
        self.failure_threshold = failure_threshold
        self._providers: dict[str, ProviderHealth] = {}
        self._memory_warnings: list[str] = []

    def register_provider(self, name: str) -> None:
        self._providers[name] = ProviderHealth(name=name)

    def record_request(self, provider: str, latency_ms: float, success: bool) -> None:
        ph = self._providers.get(provider)
        if not ph:
            return
        ph.total_requests += 1
        if not success:
            ph.failed_requests += 1
            ph.consecutive_failures += 1
            if ph.consecutive_failures >= self.failure_threshold:
                ph.is_available = False
                logger.warning(f"HealthMonitor: {provider} marked UNAVAILABLE ({ph.consecutive_failures} failures)")
        else:
            ph.consecutive_failures = 0
            ph.is_available = True
        ph.avg_latency_ms = (ph.avg_latency_ms * (ph.total_requests - 1) + latency_ms) / ph.total_requests
        ph.last_check = time.time()

    def is_available(self, provider: str) -> bool:
        ph = self._providers.get(provider)
        return ph.is_available if ph else True

    def error_rate(self, provider: str) -> float:
        ph = self._providers.get(provider)
        if not ph or ph.total_requests == 0:
            return 0.0
        return ph.failed_requests / ph.total_requests * 100

    def check_memory(self) -> dict:
        """检查内存使用"""
        import psutil
        try:
            proc = psutil.Process()
            mem = proc.memory_info()
            usage_mb = mem.rss / 1024 / 1024
            result = {"rss_mb": round(usage_mb, 1), "vms_mb": mem.vms / 1024 / 1024}
            if usage_mb > 500:
                self._memory_warnings.append(f"High memory: {usage_mb:.0f}MB at {time.time():.0f}")
            return result
        except ImportError:
            return {"rss_mb": -1, "note": "psutil not installed"}

    @property
    def stats(self) -> dict:
        return {
            provider: {
                "available": ph.is_available,
                "total_requests": ph.total_requests,
                "error_rate": f"{self.error_rate(provider):.1f}%",
                "avg_latency_ms": round(ph.avg_latency_ms, 1),
            }
            for provider, ph in self._providers.items()
        }