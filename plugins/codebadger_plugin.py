#!/usr/bin/env python3
# 灵枢 (LingShu) - 智能调度总控，道法自然，任务不辍
"""
CodebadgerInspectPlugin — 调用 Codebadger MCP Server 进行代码切片和数据流追踪
"""
from __future__ import annotations
import asyncio
import json
import logging
import re
from typing import Optional

from plugins.base import BasePlugin

logger = logging.getLogger(__name__)


class CodebadgerInspectPlugin(BasePlugin):
    """Codebadger 程序切片与数据流追踪插件"""

    name = "codebadger_inspect"
    description = "调用 Codebadger MCP Server 进行程序切片和数据流追踪"
    phase = "evidence_collection"

    _server_url: str = "http://localhost:8765"
    _timeout: int = 30

    async def init(self) -> None:
        logger.info(f"CodebadgerInspectPlugin initialized, server={self._server_url}")

    async def validate(self) -> bool:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._server_url}/health")
                return resp.status_code == 200
        except Exception as e:
            logger.warning(f"Codebadger server not available: {e}")
            return False

    async def run(self, state: dict) -> dict:
        logger.info("CodebadgerInspectPlugin: slicing code...")
        if not await self.validate():
            state["dataflow_slices"] = "Codebadger unavailable"
            state["plugin_skipped"] = state.get("plugin_skipped", []) + ["codebadger_inspect"]
            return state

        target = self._extract_target(state)
        slices = None

        if target:
            try:
                import httpx
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(
                        f"{self._server_url}/slice",
                        json={
                            "file": target["file"],
                            "function": target.get("function"),
                            "line": target.get("line"),
                        },
                    )
                    if resp.status_code == 200:
                        slices = resp.json()
            except Exception as e:
                logger.warning(f"Codebadger request failed: {e}")

        if slices:
            state["dataflow_slices"] = json.dumps(slices, ensure_ascii=False)[:5000]
            state["dataflow_raw"] = slices
        else:
            state["dataflow_slices"] = "Codebadger returned no results"

        return state

    def _extract_target(self, state: dict) -> Optional[dict]:
        if "task" in state and state["task"]:
            matches = re.findall(r"(?:in |file |src/|app/|lib/)([\w./]+\.py)", state["task"])
            if matches:
                return {"file": matches[0]}
        if "code_file_path" in state and state["code_file_path"]:
            return {"file": state["code_file_path"]}
        return None