#!/usr/bin/env python3
"""
MidTermMemory - 增强版：项目级记忆 + 语义检索
"""
from __future__ import annotations
import json
import os
import re
from datetime import datetime
from typing import Any, List, Optional


class MidTermMemory:
    """中期记忆：项目级知识积累"""

    def __init__(self, project_root: str = "."):
        self.project_root = project_root
        self._decisions: List[dict] = []
        self._patterns: dict[str, Any] = {}
        self._knowledge: dict[str, Any] = {}  # 结构化知识库
        self._storage_path = os.path.join(project_root, ".lingshu", "mid_term.json")
        self._load()

    def _load(self) -> None:
        if os.path.exists(self._storage_path):
            try:
                with open(self._storage_path) as f:
                    data = json.load(f)
                self._decisions = data.get("decisions", [])
                self._patterns = data.get("patterns", {})
                self._knowledge = data.get("knowledge", {})
            except (json.JSONDecodeError, IOError):
                pass

    def _save(self) -> None:
        os.makedirs(os.path.dirname(self._storage_path) or ".", exist_ok=True)
        with open(self._storage_path, "w") as f:
            json.dump({
                "decisions": self._decisions[-100:],
                "patterns": self._patterns,
                "knowledge": self._knowledge,
            }, f, indent=2)

    def record_decision(self, context: str, decision: str, rationale: str) -> None:
        self._decisions.append({
            "context": context,
            "decision": decision,
            "rationale": rationale,
            "timestamp": datetime.now().isoformat(),
        })
        self._save()

    def get_recent_decisions(self, n: int = 5) -> List[dict]:
        return self._decisions[-n:]

    def learn_pattern(self, name: str, pattern: Any) -> None:
        self._patterns[name] = pattern
        self._save()

    def recall_pattern(self, name: str) -> Optional[Any]:
        return self._patterns.get(name)

    def store_knowledge(self, key: str, value: Any) -> None:
        self._knowledge[key] = value
        self._save()

    def retrieve_knowledge(self, key: str) -> Optional[Any]:
        return self._knowledge.get(key)

    def semantic_search(self, query: str, top_k: int = 5) -> list[dict]:
        """跨 decisions/patterns/knowledge 的语义检索"""
        query_lower = query.lower()
        query_terms = set(re.findall(r'\w+', query_lower))
        results = []

        for d in self._decisions:
            text = f"{d['context']} {d['decision']} {d['rationale']}"
            terms = set(re.findall(r'\w+', text.lower()))
            overlap = len(query_terms & terms)
            if overlap > 0:
                results.append((overlap, {"type": "decision", **d}))

        for k, v in self._knowledge.items():
            text = f"{k} {json.dumps(v, ensure_ascii=False)}"
            terms = set(re.findall(r'\w+', text.lower()))
            overlap = len(query_terms & terms)
            if overlap > 0:
                results.append((overlap, {"type": "knowledge", "key": k, "value": v}))

        results.sort(key=lambda x: -x[0])
        return [r[1] for r in results[:top_k]]