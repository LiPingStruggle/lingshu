"""
ResourceMonitor - 资源监控与并发控制

职责:
- 限制最大并发任务数
- 跟踪每个 provider 的 RPM/TPM
- 提供 acquire/release 槽位机制
"""
from __future__ import annotations
import asyncio
import time
from typing import Dict, Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class ProviderQuota:
    """Provider 配额状态"""
    rpm_limit: int = 60
    tpm_limit: int = 200000
    rpm_used: int = 0
    tpm_used: int = 0
    window_start: float = 0.0


class ResourceMonitor:
    """资源监控与并发限制"""

    def __init__(self, max_concurrent: int = 5, global_rpm: int = 100):
        self.max_concurrent = max_concurrent
        self.global_rpm = global_rpm
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._provider_quotas: Dict[str, ProviderQuota] = {}
        self._active_tasks: Dict[str, float] = {}  # task_id -> start_time

    def register_provider(self, name: str, rpm: int = 60, tpm: int = 200000) -> None:
        """注册 provider 配额"""
        self._provider_quotas[name] = ProviderQuota(
            rpm_limit=rpm, tpm_limit=tpm, window_start=time.time()
        )

    async def acquire(self, provider: Optional[str] = None) -> bool:
        """获取执行槽位，带 provider 限流"""
        await self._semaphore.acquire()

        if provider and provider in self._provider_quotas:
            quota = self._provider_quotas[provider]
            now = time.time()

            # 重置窗口（1 分钟）
            if now - quota.window_start >= 60:
                quota.rpm_used = 0
                quota.tpm_used = 0
                quota.window_start = now

            if quota.rpm_used >= quota.rpm_limit:
                self._semaphore.release()
                logger.warning(f"Provider '{provider}' RPM limit reached")
                return False

            quota.rpm_used += 1

        return True

    def release(self) -> None:
        """释放执行槽位"""
        self._semaphore.release()

    def can_execute(self) -> bool:
        """是否有可用的执行槽位"""
        return self._semaphore.locked() is False

    @property
    def active_count(self) -> int:
        return len(self._active_tasks)

    def track_task(self, task_id: str) -> None:
        self._active_tasks[task_id] = time.time()

    def untrack_task(self, task_id: str) -> None:
        self._active_tasks.pop(task_id, None)