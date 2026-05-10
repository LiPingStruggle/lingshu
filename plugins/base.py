"""
BasePlugin - 插件抽象基类

定义所有插件的规范接口：
- name / description 类属性
- validate() 检查外部依赖是否可用
- run(state) 执行核心逻辑
"""
from __future__ import annotations
import logging
from abc import ABC, abstractmethod
from typing import Any

logger = logging.getLogger("lingshu.plugins")


class BasePlugin(ABC):
    """所有插件的抽象基类"""

    # ── 类属性（子类必须覆盖） ────────────────────────────
    name: str = ""
    description: str = ""
    phase: str = ""  # 默认注册的阶段名

    def __init__(self) -> None:
        self.enabled: bool = True
        self._validated: bool = False
        self._last_validation: bool = False

    # ── 抽象方法 ──────────────────────────────────────────

    @abstractmethod
    async def run(self, state: dict) -> dict:
        """执行插件核心逻辑。

        在实现中必须调用 self.validate() 先检查工具可用性。

        Args:
            state: 当前状态字典，包含当前分析上下文。

        Returns:
            更新后的状态字典。
        """
        ...

    # ── 具体方法 ──────────────────────────────────────────

    async def validate(self) -> bool:
        """检查该插件依赖的外部工具是否可用。

        如果检查失败，返回 False 并记录警告日志，但不抛出异常。
        子类可重写此方法实现具体检测逻辑。
        """
        return True

    async def init(self) -> None:
        """插件初始化钩子（可选覆盖）"""
        pass

    async def cleanup(self) -> None:
        """插件清理钩子（可选覆盖）"""
        pass

    # ── 内置辅助 ──────────────────────────────────────────

    def __repr__(self) -> str:
        return f"<{type(self).__name__} name={self.name!r} enabled={self.enabled}>"

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "phase": self.phase,
            "enabled": self.enabled,
        }