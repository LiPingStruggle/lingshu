#!/usr/bin/env python3
# 灵枢 (LingShu) - 智能调度总控，道法自然，任务不辍
"""
TrailmarkCallgraphPlugin — 调用 Trailmark 生成项目调用图
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
from typing import Optional

from plugins.base import BasePlugin

logger = logging.getLogger(__name__)


class TrailmarkCallgraphPlugin(BasePlugin):
    """Trailmark 调用图生成插件"""

    name = "trailmark_callgraph"
    description = "调用 Trailmark 生成项目调用图，辅助精准定位影响面"
    phase = "evidence_collection"

    _cli_path: str = "trailmark"
    _output_dir: str = ".lingshu/trailmark"

    async def init(self) -> None:
        os.makedirs(self._output_dir, exist_ok=True)

    async def validate(self) -> bool:
        try:
            proc = await asyncio.create_subprocess_exec(
                self._cli_path, "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            ok = proc.returncode == 0
            logger.info(f"Trailmark {'available' if ok else 'not found'}: {stdout.decode().strip()}")
            return ok
        except (FileNotFoundError, asyncio.TimeoutError, OSError):
            logger.warning("Trailmark CLI not available")
            return False

    async def run(self, state: dict) -> dict:
        logger.info("TrailmarkCallgraphPlugin: generating call graph...")
        if not await self.validate():
            state["call_graph_summary"] = "Trailmark unavailable"
            state["plugin_skipped"] = state.get("plugin_skipped", []) + ["trailmark_callgraph"]
            return state

        call_graph = None
        try:
            out_path = os.path.join(self._output_dir, "callgraph.json")
            proc = await asyncio.create_subprocess_exec(
                self._cli_path, "callgraph", "--format", "json",
                "--output", out_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)
            if proc.returncode == 0 and os.path.exists(out_path):
                with open(out_path) as f:
                    call_graph = json.load(f)
            else:
                logger.warning(f"Trailmark failed: {stderr.decode()[:200]}")
        except (FileNotFoundError, asyncio.TimeoutError, OSError) as e:
            logger.warning(f"Trailmark execution failed: {e}")

        if call_graph:
            state["call_graph_summary"] = json.dumps(call_graph, ensure_ascii=False)[:5000]
            state["call_graph_raw"] = call_graph
        else:
            state["call_graph_summary"] = "Trailmark execution failed"

        return state