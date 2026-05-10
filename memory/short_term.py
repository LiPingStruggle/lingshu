#!/usr/bin/env python3
"""
ShortTermMemory - 增强版：当前任务记忆 + 语义检索
"""
from __future__ import annotations
import json
import re
from datetime import datetime
from typing import Any, Optional


class ShortTermMemory:
    """短期记忆：当前任务上下文"""

    def __init__(self):
        self._storage: dict[str, Any] = {}
        self._task_stack: list = []
        self._message_history: list[dict] = []  # 用于语义检索

    def remember(self, key: str, value: Any) -> None:
        self._storage[key] = value
        self._storage["_updated_at"] = datetime.now().isoformat()

    def recall(self, key: str) -> Optional[Any]:
        return self._storage.get(key)

    def push_task(self, task_id: str) -> None:
        self._task_stack.append(task_id)

    def pop_task(self) -> Optional[str]:
        if self._task_stack:
            return self._task_stack.pop()
        return None

    def current_task(self) -> Optional[str]:
        if self._task_stack:
            return self._task_stack[-1]
        return None

    def add_message(self, role: str, content: str) -> None:
        self._message_history.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        })
        # 只保留最近 100 条
        if len(self._message_history) > 100:
            self._message_history = self._message_history[-100:]

    def semantic_search(self, query: str, top_k: int = 5) -> list[dict]:
        """简单的关键词检索（无需 embedding 模型）"""
        query_lower = query.lower()
        query_terms = set(re.findall(r'\w+', query_lower))

        scored = []
        for msg in reversed(self._message_history):
            content_lower = msg["content"].lower()
            terms = set(re.findall(r'\w+', content_lower))
            overlap = len(query_terms & terms)
            if overlap > 0:
                scored.append((overlap, msg))

        scored.sort(key=lambda x: -x[0])
        return [s[1] for s in scored[:top_k]]

    def get_recent_messages(self, n: int = 10) -> list[dict]:
        return self._message_history[-n:]

    def clear(self) -> None:
        self._storage.clear()
        self._task_stack.clear()
        self._message_history.clear()