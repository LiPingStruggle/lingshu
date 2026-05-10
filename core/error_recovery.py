#!/usr/bin/env python3
"""
ErrorRecovery - 错误恢复与 Watchdog

8 种错误分类 + 恢复策略:
  rate_limit       → backoff_retry (指数退避 + 抖动)
  quota_exceeded   → model_downgrade
  context_too_long → context_compress
  auth_error       → abort (不可恢复)
  model_not_found  → model_fallback
  network_error    → backoff_retry
  invalid_output   → rephrase_retry
  unknown          → generic_retry
"""
from __future__ import annotations
import asyncio
import time
import random
import logging
from typing import Optional, Callable, Awaitable
from workflows.task_workflow import Task

logger = logging.getLogger(__name__)


class Watchdog:
    """看门狗定时器，超时自动触发回调"""

    def __init__(self, timeout: float = 300.0):
        self.timeout = timeout
        self._task: Optional[asyncio.Task] = None
        self._callback: Optional[Callable] = None

    async def _run(self, task_id: str) -> None:
        await asyncio.sleep(self.timeout)
        logger.warning(f"Watchdog triggered for task {task_id} (timeout={self.timeout}s)")
        if self._callback:
            await self._callback(task_id)

    def start(self, task_id: str, callback: Callable[[str], Awaitable[None]]) -> None:
        self._callback = callback
        self._task = asyncio.create_task(self._run(task_id))

    def cancel(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None


class ErrorRecovery:
    """错误恢复引擎"""

    ERROR_CATEGORIES = {
        "rate_limit": {
            "keywords": ["rate_limit", "rate limit", "too many requests", "429", "请勿频繁", "请求过多"],
            "recoverable": True,
            "strategy": "backoff_retry",
            "max_retries": 5,
        },
        "quota_exceeded": {
            "keywords": ["quota", "insufficient_quota", "exceeded", "credit", "余额不足", "配额"],
            "recoverable": True,
            "strategy": "model_downgrade",
            "max_retries": 3,
        },
        "context_too_long": {
            "keywords": ["context_length", "too long", "maximum context", "token limit", "tokens", "上下文过长", "超长"],
            "recoverable": True,
            "strategy": "context_compress",
            "max_retries": 2,
        },
        "auth_error": {
            "keywords": ["auth", "api_key", "unauthorized", "401", "403", "invalid key", "认证失败", "密钥"],
            "recoverable": False,
            "strategy": "abort",
            "max_retries": 0,
        },
        "model_not_found": {
            "keywords": ["model_not_found", "model not found", "not support", "not found", "找不到模型"],
            "recoverable": True,
            "strategy": "model_fallback",
            "max_retries": 2,
        },
        "network_error": {
            "keywords": ["connection", "timeout", "network", "econnreset", "econnrefused", "socket", "连接", "网络"],
            "recoverable": True,
            "strategy": "backoff_retry",
            "max_retries": 3,
        },
        "invalid_output": {
            "keywords": ["invalid", "parse", "json", "格式错误"],
            "recoverable": True,
            "strategy": "rephrase_retry",
            "max_retries": 2,
        },
        "unknown": {
            "keywords": [],
            "recoverable": True,
            "strategy": "generic_retry",
            "max_retries": 2,
        },
    }

    def __init__(self, model_router=None):
        self.model_router = model_router
        self.watchdog: Optional[Watchdog] = None

    def classify_error(self, error: str) -> str:
        """根据错误消息分类"""
        error_lower = error.lower()
        for category, config in self.ERROR_CATEGORIES.items():
            for kw in config["keywords"]:
                if kw.lower() in error_lower:
                    return category
        return "unknown"

    def is_recoverable(self, category: str) -> bool:
        config = self.ERROR_CATEGORIES.get(category)
        return config["recoverable"] if config else False

    def get_strategy(self, category: str) -> str:
        config = self.ERROR_CATEGORIES.get(category)
        return config["strategy"] if config else "generic_retry"

    def get_max_retries(self, category: str) -> int:
        config = self.ERROR_CATEGORIES.get(category)
        return config["max_retries"] if config else 2

    async def recover(self, task: Task, error: str, attempt: int = 1,
                      model_router=None, on_retry=None) -> bool:
        """
        尝试恢复任务执行
        返回 True 表示恢复成功可以重试，False 表示不可恢复
        """
        category = self.classify_error(error)
        max_retries = self.get_max_retries(category)
        strategy = self.get_strategy(category)

        logger.info(f"Error recovery: category={category}, strategy={strategy}, attempt={attempt}/{max_retries}")

        if not self.is_recoverable(category):
            logger.error(f"Unrecoverable error '{category}', aborting task {task.task_id}")
            task.status = "failed"
            task.error = f"Unrecoverable: {category}: {error}"
            return False

        if attempt > max_retries:
            logger.error(f"Max retries ({max_retries}) exceeded for task {task.task_id}")
            task.status = "failed"
            task.error = f"Max retries exceeded: {error}"
            return False

        # 指数退避
        if strategy == "backoff_retry":
            wait = min(2 ** attempt + random.uniform(0, 1), 60)
            logger.info(f"Backing off {wait:.1f}s before retry")
            await asyncio.sleep(wait)
            return True

        # 模型降级
        if strategy == "model_downgrade" and model_router:
            current = getattr(task, "_current_model", None)
            if current:
                fallback = model_router.get_fallback(current)
                if fallback:
                    task._current_model = fallback
                    logger.info(f"Downgraded model to {fallback}")
                    return True

        # 模型切换
        if strategy == "model_fallback" and model_router:
            current = getattr(task, "_current_model", None)
            if current:
                fallback = model_router.get_fallback(current)
                if fallback:
                    task._current_model = fallback
                    logger.info(f"Fallback model to {fallback}")
                    return True

        # 默认重试
        return True

    def start_watchdog(self, task_id: str, timeout: float,
                       callback: Callable[[str], Awaitable[None]]) -> Watchdog:
        """启动看门狗"""
        self.watchdog = Watchdog(timeout)
        self.watchdog.start(task_id, callback)
        return self.watchdog

    def stop_watchdog(self) -> None:
        if self.watchdog:
            self.watchdog.cancel()
            self.watchdog = None