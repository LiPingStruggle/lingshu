#!/usr/bin/env python3
"""
SamplePlugin - 插件示例（基于 BasePlugin）

展示如何基于 BasePlugin 编写一个符合新架构的插件。
"""
from __future__ import annotations
import logging

from plugins.base import BasePlugin

logger = logging.getLogger(__name__)


class SamplePlugin(BasePlugin):
    """示例插件 — 演示 BasePlugin 接口用法"""

    name = "sample-plugin"
    description = "一个演示用的示例插件"
    phase = "post_execution"

    async def validate(self) -> bool:
        # 示例：总是可用
        return True

    async def run(self, state: dict) -> dict:
        logger.info("SamplePlugin.run() called with state keys: %s", list(state.keys()))
        state["sample_plugin_ran"] = True
        state["sample_message"] = "Hello from SamplePlugin!"
        return state