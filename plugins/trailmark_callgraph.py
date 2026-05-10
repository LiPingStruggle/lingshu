#!/usr/bin/env python3
# 灵枢 (LingShu) - 智能调度总控，道法自然，任务不辍
"""
TrailmarkCallgraphPlugin - Trailmark 调用图分析插件

在 evidence_collection 阶段收集项目调用图，
辅助 Plan 模型评估每个改动的影响面。
"""
from __future__ import annotations
import asyncio
import json
import logging
import subprocess
from typing import Any

from plugins.base import BasePlugin

logger = logging.getLogger("lingshu.plugins.trailmark")


class TrailmarkCallgraphPlugin(BasePlugin):
    """调用 Trailmark 生成项目调用图"""

    name = "trailmark_callgraph"
    description = "调用图分析 — 生成项目函数调用关系图，评估改动影响面"
    phase = "evidence_collection"

    async def validate(self) -> bool:
        """检查 trailmark CLI 是否可用"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "trailmark", "--version",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode == 0:
                logger.info("trailmark CLI available: %s", stdout.decode().strip())
                return True
        except FileNotFoundError:
            logger.warning("trailmark CLI not found, plugin will be skipped")
        except Exception as e:
            logger.warning("trailmark validation failed: %s", e)
        return False

    async def run(self, state: dict) -> dict:
        """执行调用图分析"""
        if not await self.validate():
            state.setdefault("plugin_skipped", []).append(self.name)
            return state

        logger.info("Running trailmark callgraph analysis...")
        try:
            proc = await asyncio.create_subprocess_exec(
                "trailmark", "callgraph", "--format", "json",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60)

            if proc.returncode == 0 and stdout:
                raw = json.loads(stdout.decode())
                state["call_graph_summary"] = self._summarize(raw)
                state.setdefault("plugin_results", {})[self.name] = "ok"
                logger.info("trailmark callgraph completed")
            else:
                logger.warning("trailmark failed: %s", stderr.decode()[:200])
                state["call_graph_summary"] = "trailmark callgraph unavailable"
        except asyncio.TimeoutError:
            logger.warning("trailmark timed out after 60s")
            state["call_graph_summary"] = "trailmark callgraph timed out"
        except Exception as e:
            logger.error("trailmark run failed: %s", e)
            state.setdefault("plugin_errors", {})[self.name] = str(e)

        return state

    @staticmethod
    def _summarize(raw: list | dict) -> str:
        """将原始调用图压缩为摘要"""
        if isinstance(raw, list):
            entries = raw[:50]
        elif isinstance(raw, dict):
            entries = list(raw.items())[:50]
        else:
            entries = [str(raw)[:1000]]
        return json.dumps(entries, ensure_ascii=False, indent=2)[:2000]