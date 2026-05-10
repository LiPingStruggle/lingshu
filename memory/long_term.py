#!/usr/bin/env python3
"""
LongTermMemory - 增强版：跨项目经验 + 持久化语义检索
"""
from __future__ import annotations
import json
import os
import re
from typing import Any, Optional


class LongTermMemory:
    """长期记忆：跨项目经验"""

    def __init__(self, storage_path: str = ".lingshu/memory.json"):
        self.storage_path = storage_path
        self._data: dict[str, Any] = self._load()
        self._index: dict[str, list[str]] = {}  # term -> keys
        self._rebuild_index()

    def _load(self) -> dict:
        if os.path.exists(self.storage_path):
            try:
                with open(self.storage_path) as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return {}
        return {}

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self.storage_path) or ".", exist_ok=True)
        with open(self.storage_path, "w") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    def _rebuild_index(self) -> None:
        self._index.clear()
        for key, value in self._data.items():
            text = f"{key} {json.dumps(value, ensure_ascii=False)}"
            terms = set(re.findall(r'\w+', text.lower()))
            for term in terms:
                if term not in self._index:
                    self._index[term] = []
                self._index[term].append(key)

    def store(self, key: str, value: Any) -> None:
        self._data[key] = value
        self._save()
        self._rebuild_index()

    def retrieve(self, key: str) -> Optional[Any]:
        return self._data.get(key)

    def list_keys(self) -> list:
        return list(self._data.keys())

    def semantic_search(self, query: str, top_k: int = 5) -> list[dict]:
        """基于倒排索引的关键词检索"""
        from collections import Counter
        query_terms = set(re.findall(r'\w+', query.lower()))

        scores = Counter()
        for term in query_terms:
            if term in self._index:
                for key in self._index[term]:
                    scores[key] += 1

        results = []
        for key, score in scores.most_common(top_k):
            results.append({
                "key": key,
                "value": self._data[key],
                "score": score,
            })
        return results

    def get_stats(self) -> dict:
        return {
            "total_entries": len(self._data),
            "index_terms": len(self._index),
            "storage_path": self.storage_path,
        }