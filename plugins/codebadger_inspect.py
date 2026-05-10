#!/usr/bin/env python3
# 灵枢 (LingShu) - 智能调度总控，道法自然，任务不辍
"""
CodebadgerInspectPlugin - Codebadger 代码缺陷审查插件

在 evidence_collection 阶段对代码进行数据流切片和缺陷检测，
输出问题代码的精准位置。
"""
from __future__ import annotations
import asyncio
import json
import logging
import subprocess
from typing import Any

from plugins.base import BasePlugin

logger = logging.getLogger("lingshu.plugins.codebadger")


class CodebadgerInspectPlugin(BasePlugin):
    """调用 Codebadger MCP Server 进行程序切片和数据流追踪"""

    name = "codebadger_inspect"
    description = "代码缺陷审查 — 程序切片 + 数据流追踪，精准定位问题代码"
    phase = "evidence_collection"

    async def validate(self) -> bool:
        """检查 codebadger CLI 是否可用"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "codebadger", "--version",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode == 0:
                logger.info("codebadger CLI available: %s", stdout.decode().strip())
                return True
        except FileNotFoundError:
            logger.warning("codebadger CLI not found, plugin will be skipped")
        except Exception as e:
            logger.warning("codebadger validation failed: %s", e)
        return False

    async def run(self, state: dict) -> dict:
        """执行代码缺陷审查"""
        if not await self.validate():
            state.setdefault("plugin_skipped", []).append(self.name)
            return state

        logger.info("Running codebadger inspection...")

        # 从 state 获取文件路径
        code_path = state.get("code_file_path", "") or state.get("file_path", "")

        try:
            cmd = ["codebadger", "inspect"]
            if code_path:
                cmd.extend(["--file", code_path])
            cmd.extend(["--format", "json"])

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=120)

            if proc.returncode == 0 and stdout:
                raw = json.loads(stdout.decode())
                state["dataflow_slices"] = self._summarize(raw)
                state.setdefault("plugin_results", {})[self.name] = "ok"
                logger.info("codebadger inspection completed")
            else:
                logger.warning("codebadger failed: %s", stderr.decode()[:200])
                state["dataflow_slices"] = "codebadger inspection unavailable"

        except asyncio.TimeoutError:
            logger.warning("codebadger timed out after 120s")
            state["dataflow_slices"] = "codebadger inspection timed out"
        except Exception as e:
            logger.error("codebadger run failed: %s", e)
            state.setdefault("plugin_errors", {})[self.name] = str(e)

        return state

    def _summarize(self, raw: list | dict) -> str:
        """压缩数据流切片为摘要"""
        if isinstance(raw, list):
            entries = raw[:30]
        elif isinstance(raw, dict):
            entries = dict(list(raw.items())[:30])
        else:
            return str(raw)[:2000]
        return json.dumps(entries, ensure_ascii=False, indent=2)[:2000]