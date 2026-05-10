"""
ModelRouter - 4 级模型路由与自动故障转移

职责:
- 注册模型提供方与层级
- 按 role/task_type 选择最佳模型
- 故障时自动 fallback 到更低/更高层级
"""
from __future__ import annotations
from typing import Dict, List, Optional, Callable
from dataclasses import dataclass, field
import logging

logger = logging.getLogger(__name__)


@dataclass
class ModelConfig:
    """模型配置"""
    name: str
    provider: str  # openai | anthropic | google | ollama | deepseek
    tier: int      # 5=elite 4=strong 3=medium 2=light 1=local 0=mock
    context_window: int = 128000
    max_tokens: int = 4096
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    rpm_limit: int = 60
    tpm_limit: int = 200000


TIER_NAMES = {
    5: "elite",
    4: "strong",
    3: "medium",
    2: "light",
    1: "local",
    0: "mock",
}

# 角色最小层级门槛
ROLE_MIN_TIER = {
    "analyzer": 4,      # Strong
    "root_cause": 5,    # Elite
    "reviewer": 4,      # Strong
    "architect": 4,     # Strong
    "worker": 2,        # Light
    "editor": 2,        # Light
    "advisor": 5,       # Elite
}

# 任务类型到 role 的映射
TASK_TYPE_ROLE = {
    "bug_fix": "root_cause",
    "refactor": "architect",
    "test_generation": "analyzer",
    "code_review": "reviewer",
    "generic": "worker",
}


class ModelRouter:
    """模型路由，管理多模型注册、选择和故障转移"""

    def __init__(self):
        self._models: Dict[str, ModelConfig] = {}
        self._tier_index: Dict[int, List[str]] = {t: [] for t in range(6)}

    def register_model(self, name: str, provider: str, tier: int, **kwargs) -> None:
        """注册一个模型到路由表"""
        config = ModelConfig(name=name, provider=provider, tier=tier, **kwargs)
        self._models[name] = config
        self._tier_index[tier].append(name)
        logger.info(f"Registered model: {name} ({provider}, tier={tier})")

    def register_default_models(self) -> None:
        """注册内置默认模型"""
        defaults = [
            # Elite (5)
            ("claude-sonnet-4", "anthropic", 5, {"context_window": 200000}),
            # Strong (4)
            ("gpt-4.1", "openai", 4, {"context_window": 128000}),
            ("deepseek-chat", "deepseek", 4, {"context_window": 128000, "base_url": "https://api.deepseek.com/v1"}),
            # Medium (3)
            ("gpt-4o-mini", "openai", 3, {"context_window": 128000}),
            # Light (2)
            ("LongCat-Flash-Lite", "longcat", 2, {
                "context_window": 128000,
                "base_url": "https://api.longcat.chat/openai/v1",
                "api_key": "ak_2Y39CK78A6DI6ry4El89o29P6Hb65",
            }),
            ("gemini-2.0-flash", "google", 2, {"context_window": 1000000}),
            # Local (1)
            ("qwen2", "ollama", 1, {"context_window": 32768, "base_url": "http://localhost:11434/v1"}),
            # Mock (0)
            ("mock-model", "mock", 0, {"context_window": 4096}),
        ]
        for name, provider, tier, kwargs in defaults:
            self.register_model(name, provider, tier, **kwargs)

    def select_model(self, role: str, task_type: Optional[str] = None) -> Optional[str]:
        """
        为指定 role 选择最佳模型
        1. 先查 role 的最低层级门槛
        2. 从该层级开始向下查找可用模型
        3. 如果本层没有，降级到下一层
        """
        min_tier = ROLE_MIN_TIER.get(role, 2)

        for tier in range(min_tier, -1, -1):
            models = self._tier_index.get(tier, [])
            if models:
                selected = models[0]
                logger.debug(f"Selected model '{selected}' for role '{role}' (tier={tier})")
                return selected

        logger.warning(f"No model available for role '{role}'")
        return None

    def get_fallback(self, current_model: str) -> Optional[str]:
        """
        获取故障转移模型
        fallback 链: local(1) → light(2) → medium(3) → strong(4) → elite(5)
        """
        config = self._models.get(current_model)
        if not config:
            return None

        current_tier = config.tier
        for tier in range(current_tier + 1, 6):
            models = self._tier_index.get(tier, [])
            if models:
                fallback = models[0]
                logger.info(f"Fallback from '{current_model}' (tier={current_tier}) to '{fallback}' (tier={tier})")
                return fallback

        return None

    def get_model_config(self, name: str) -> Optional[ModelConfig]:
        """获取模型配置"""
        return self._models.get(name)

    def list_models(self, tier: Optional[int] = None) -> List[str]:
        """列出所有模型或指定层级的模型"""
        if tier is not None:
            return self._tier_index.get(tier, [])
        return list(self._models.keys())