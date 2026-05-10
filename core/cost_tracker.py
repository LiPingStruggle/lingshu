"""
CostTracker - 成本追踪

需求覆盖（第 8 章）：按 provider 统计 token 使用和费用
"""
from __future__ import annotations
import json
import os
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class CostEntry:
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    timestamp: float = 0.0

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()


class CostTracker:
    """成本追踪器"""

    MODEL_COST_MAP = {
        "gpt-4": {"input": 30.0, "output": 60.0},
        "gpt-4o-mini": {"input": 0.15, "output": 0.60},
        "gpt-4.1": {"input": 10.0, "output": 40.0},
        "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
        "claude-sonnet-4-20250514": {"input": 15.0, "output": 75.0},
        "claude-3.5-sonnet": {"input": 3.0, "output": 15.0},
        "claude-3.5-haiku": {"input": 0.80, "output": 4.0},
        "gemini-2.0-flash": {"input": 0.10, "output": 0.40},
        "deepseek-chat": {"input": 0.50, "output": 2.0},
    }

    def __init__(self, cost_dir: str = ".lingshu/costs"):
        self.cost_dir = cost_dir
        self._entries: list[CostEntry] = []
        os.makedirs(cost_dir, exist_ok=True)

    def record(self, provider: str, model: str, input_tokens: int,
               output_tokens: int) -> CostEntry:
        """记录一次调用成本"""
        rate = self.MODEL_COST_MAP.get(model, {"input": 1.0, "output": 2.0})
        cost_usd = (input_tokens / 1_000_000 * rate["input"] +
                    output_tokens / 1_000_000 * rate["output"])

        entry = CostEntry(
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=round(cost_usd, 6),
        )
        self._entries.append(entry)
        return entry

    def total_cost(self) -> float:
        return round(sum(e.cost_usd for e in self._entries), 4)

    def total_tokens(self) -> dict:
        return {
            "input": sum(e.input_tokens for e in self._entries),
            "output": sum(e.output_tokens for e in self._entries),
        }

    def by_provider(self) -> dict:
        """按 provider 汇总"""
        result = {}
        for e in self._entries:
            if e.provider not in result:
                result[e.provider] = {"requests": 0, "cost": 0.0, "input_tokens": 0, "output_tokens": 0}
            result[e.provider]["requests"] += 1
            result[e.provider]["cost"] += e.cost_usd
            result[e.provider]["input_tokens"] += e.input_tokens
            result[e.provider]["output_tokens"] += e.output_tokens
        return {k: {**v, "cost": round(v["cost"], 4)} for k, v in result.items()}

    @property
    def stats(self) -> dict:
        return {
            "total_requests": len(self._entries),
            "total_cost_usd": self.total_cost(),
            "total_tokens": self.total_tokens(),
            "by_provider": self.by_provider(),
        }