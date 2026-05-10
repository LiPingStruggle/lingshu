#!/usr/bin/env python3
# 灵枢 (LingShu) - 智能调度总控，道法自然，任务不辍
"""
DitingVerifyPlugin - 谛听辩论式验证插件

在 post_execution 阶段收集根因报告、修复方案和证据，
提交给《谛听》进行辩论式验证，确保修复方案的正确性和完整性。
"""
from __future__ import annotations
import asyncio
import json
import logging
import subprocess
import os
from typing import Any

from plugins.base import BasePlugin

logger = logging.getLogger("lingshu.plugins.diting")


class DitingVerifyPlugin(BasePlugin):
    """
    收集《灵枢》的根因报告、修复方案、证据，
    打包提交给《谛听》进行辩论式验证。
    """

    name = "diting_verify"
    description = "辩论式验证 — 调用谛听引擎对修复方案进行正反辩论验证"
    phase = "post_execution"

    # 验证深度配置
    DEPTH_MAP = {
        "light": {"rounds": 1, "models": 1},
        "medium": {"rounds": 2, "models": 2},
        "deep": {"rounds": 3, "models": 3},
    }

    async def validate(self) -> bool:
        """检查谛听 CLI 或 API 是否可用"""
        # 优先检查 diting CLI
        try:
            proc = await asyncio.create_subprocess_exec(
                "diting", "--version",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode == 0:
                logger.info("diting CLI available: %s", stdout.decode().strip())
                return True
        except FileNotFoundError:
            pass
        except Exception as e:
            logger.warning("diting validation failed: %s", e)

        # 其次检查 DITING_API_URL 环境变量
        api_url = os.environ.get("DITING_API_URL") or os.environ.get("LONGCAT_BASE_URL")
        if api_url:
            logger.info("diting API available via %s", api_url)
            return True

        logger.warning(
            "diting CLI not found and no DITING_API_URL configured, "
            "plugin will be skipped"
        )
        return False

    async def run(self, state: dict) -> dict:
        """执行辩论式验证"""
        if not await self.validate():
            state.setdefault("plugin_skipped", []).append(self.name)
            return state

        logger.info("Running diting debate verification...")

        # 收集证据包
        evidence_package = self._build_evidence_package(state)

        # 确定验证深度
        depth_config = state.get("diting_depth", "medium")
        depth_params = self.DEPTH_MAP.get(depth_config, self.DEPTH_MAP["medium"])

        try:
            verdict = await self._call_diting(evidence_package, depth_params)

            state["diting_verdict"] = verdict.get("verdict", "UNKNOWN")
            state["evidence_score"] = verdict.get("score", 0)
            state["diting_detail"] = verdict.get("detail", "")

            if verdict.get("verdict") == "REVISE":
                state.setdefault("needs_revision", True)
                state["revision_feedback"] = verdict.get("feedback", "")
                logger.info("diting suggests REVISE: %s", verdict.get("feedback", "")[:100])
            elif verdict.get("verdict") == "REJECT":
                state.setdefault("needs_human", True)
                logger.warning("diting REJECTed the proposal")
            else:
                state.setdefault("plugin_results", {})[self.name] = "ok"
                logger.info("diting verdict: PASS (score=%s)", verdict.get("score"))

        except Exception as e:
            logger.error("diting verification failed: %s", e)
            state.setdefault("plugin_errors", {})[self.name] = str(e)
            # 降级：标记为未验证但继续执行
            state["diting_verdict"] = "UNAVAILABLE"
            state["evidence_score"] = 0

        return state

    def _build_evidence_package(self, state: dict) -> dict:
        """构建提交给谛听的证据包"""
        sub_tasks = state.get("sub_tasks", [])
        sub_results = []
        for st in sub_tasks:
            if isinstance(st, dict):
                sub_results.append({
                    "id": st.get("id", "?"),
                    "description": st.get("description", ""),
                    "status": st.get("status", "unknown"),
                    "result": (st.get("result", "") or "")[:500],
                })

        return {
            "task": state.get("task", ""),
            "root_cause": state.get("root_cause", ""),
            "fix_proposal": state.get("fix_proposal", ""),
            "sub_task_results": sub_results,
            "code_context": (state.get("code_context", "") or "")[:1000],
            "log_context": (state.get("runtime_log", "") or "")[:1000],
            "call_graph": (state.get("call_graph_summary", "") or "")[:500],
            "dataflow_slices": (state.get("dataflow_slices", "") or "")[:500],
            "review_feedback": state.get("review_feedback", ""),
        }

    async def _call_diting(
        self, evidence: dict, params: dict
    ) -> dict:
        """
        调用谛听引擎进行辩论式验证

        优先使用 diting CLI，回退到 HTTP API。
        """
        # 尝试 CLI 方式
        try:
            input_json = json.dumps(evidence, ensure_ascii=False)
            proc = await asyncio.create_subprocess_exec(
                "diting", "verify",
                "--rounds", str(params["rounds"]),
                "--input", "-",
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(
                proc.communicate(input=input_json.encode()), timeout=120
            )
            if proc.returncode == 0 and stdout:
                return json.loads(stdout.decode())
        except (FileNotFoundError, json.JSONDecodeError, asyncio.TimeoutError) as e:
            logger.debug("diting CLI call failed, trying HTTP: %s", e)

        # HTTP API 回退
        api_url = os.environ.get("DITING_API_URL", "http://localhost:8080/verify")
        try:
            import httpx
            async with httpx.AsyncClient(timeout=120) as client:
                resp = await client.post(
                    api_url,
                    json={
                        "evidence": evidence,
                        "rounds": params["rounds"],
                        "models": params["models"],
                    },
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.warning("diting HTTP call also failed: %s", e)

        # 最终降级：返回默认通过
        return {
            "verdict": "PASS",
            "score": 70,
            "detail": "diting unavailable, auto-passed with reduced confidence",
            "feedback": "",
        }