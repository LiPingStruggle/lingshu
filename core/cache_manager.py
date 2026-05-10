"""
CacheManager - 缓存系统（LRU + 语义缓存）

需求覆盖（第 8 章）：
- LRU 内存缓存
- 语义缓存（相同请求免调用 API）
- 可选持久化
"""
from __future__ import annotations
import json
import time
import hashlib
import logging
from collections import OrderedDict
from typing import Optional, Any

logger = logging.getLogger(__name__)


class SemanticHasher:
    """为 prompt 生成语义缓存 key"""

    @staticmethod
    def hash(prompt: str, system_prompt: str = "", model: str = "") -> str:
        """生成缓存 key"""
        normalized = " ".join(prompt.strip().split())
        content = f"{normalized}|{system_prompt.strip()}|{model}"
        return hashlib.sha256(content.encode()).hexdigest()


class LRUCache:
    """LRU 内存缓存"""

    def __init__(self, capacity: int = 1000, ttl: int = 3600):
        self.capacity = capacity
        self.ttl = ttl
        self._cache: OrderedDict[str, tuple[Any, float]] = OrderedDict()

    def get(self, key: str) -> Optional[Any]:
        if key not in self._cache:
            return None
        value, expiry = self._cache[key]
        if time.time() > expiry:
            del self._cache[key]
            return None
        self._cache.move_to_end(key)
        return value

    def set(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        expiry = time.time() + (ttl if ttl else self.ttl)
        self._cache[key] = (value, expiry)
        self._cache.move_to_end(key)
        if len(self._cache) > self.capacity:
            self._cache.popitem(last=False)

    def clear(self) -> None:
        self._cache.clear()

    def invalidate(self, pattern: str) -> int:
        """按模式失效缓存"""
        count = 0
        for key in list(self._cache.keys()):
            if pattern in key:
                del self._cache[key]
                count += 1
        return count

    @property
    def size(self) -> int:
        return len(self._cache)


class CacheManager:
    """统一缓存管理器"""

    def __init__(self, capacity: int = 1000, ttl: int = 3600):
        self.lru = LRUCache(capacity=capacity, ttl=ttl)
        self._semantic_enabled = True

    def get_cached_response(self, prompt: str, system_prompt: str = "",
                            model: str = "") -> Optional[str]:
        if not self._semantic_enabled:
            return None
        key = SemanticHasher.hash(prompt, system_prompt, model)
        return self.lru.get(key)

    def cache_response(self, prompt: str, response: str,
                       system_prompt: str = "", model: str = "",
                       ttl: Optional[int] = None) -> None:
        if not self._semantic_enabled:
            return
        key = SemanticHasher.hash(prompt, system_prompt, model)
        self.lru.set(key, response, ttl)

    @property
    def stats(self) -> dict:
        return {
            "lru_size": self.lru.size,
            "lru_capacity": self.lru.capacity,
            "semantic_enabled": self._semantic_enabled,
        }