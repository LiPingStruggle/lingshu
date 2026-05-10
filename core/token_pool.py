"""
TokenPool - Token 池（多 Key 轮询 + 自动故障转移）

需求覆盖（第 8 章）：
- 环境变量加载多 Key
- provider 内自动轮询
- 单 key 失效自动故障转移
"""
from __future__ import annotations
import os
import random
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class APIKey:
    key: str
    provider: str
    is_valid: bool = True
    error_count: int = 0
    request_count: int = 0


class TokenPool:
    """多 Key 轮询池"""

    PROVIDER_KEY_MAP = {
        "openai": "OPENAI_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
        "google": "GEMINI_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
    }

    def __init__(self):
        self._keys: dict[str, list[APIKey]] = {}
        self._index: dict[str, int] = {}
        self._auto_load()

    def _auto_load(self) -> None:
        """从环境变量自动加载 Key"""
        for provider, env_var in self.PROVIDER_KEY_MAP.items():
            value = os.getenv(env_var)
            if value:
                # 支持逗号分隔多 Key
                for k in value.split(","):
                    k = k.strip()
                    if k:
                        self.add_key(provider, k)
                        logger.info(f"TokenPool: loaded key for {provider}")

        # 也扫描通用 KEY 格式
        for env_name, env_value in sorted(os.environ.items()):
            if env_value and ("API_KEY" in env_name or "api_key" in env_name):
                provider = env_name.replace("_API_KEY", "").replace("_api_key", "").lower()
                if provider not in self._keys:
                    for k in env_value.split(","):
                        k = k.strip()
                        if k:
                            self.add_key(provider, k)
                            logger.info(f"TokenPool: auto-loaded key from {env_name}")

    def add_key(self, provider: str, key: str) -> None:
        if provider not in self._keys:
            self._keys[provider] = []
            self._index[provider] = 0
        self._keys[provider].append(APIKey(key=key, provider=provider))

    def get_key(self, provider: str) -> Optional[str]:
        """轮询获取一个可用 Key"""
        keys = self._keys.get(provider)
        if not keys:
            logger.warning(f"TokenPool: no keys for {provider}")
            return None

        valid = [k for k in keys if k.is_valid]
        if not valid:
            logger.error(f"TokenPool: all keys exhausted for {provider}")
            # 尝试重置所有 Key
            for k in keys:
                k.is_valid = True
                k.error_count = 0
            valid = keys

        idx = self._index.get(provider, 0) % len(valid)
        self._index[provider] = idx + 1
        key = valid[idx]
        key.request_count += 1
        return key.key

    def mark_failed(self, provider: str, key: str) -> None:
        """标记 Key 失效"""
        for k in self._keys.get(provider, []):
            if k.key == key:
                k.error_count += 1
                if k.error_count >= 3:
                    k.is_valid = False
                    logger.warning(f"TokenPool: key for {provider} marked invalid after {k.error_count} errors")
                break

    def mark_success(self, provider: str, key: str) -> None:
        """标记 Key 成功"""
        for k in self._keys.get(provider, []):
            if k.key == key:
                k.error_count = 0
                break

    @property
    def stats(self) -> dict:
        return {
            provider: {
                "total": len(keys),
                "valid": sum(1 for k in keys if k.is_valid),
                "total_requests": sum(k.request_count for k in keys),
            }
            for provider, keys in self._keys.items()
        }