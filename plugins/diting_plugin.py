#!/usr/bin/env python3
# 灵枢 (LingShu) - 智能调度总控，道法自然，任务不辍
"""
DitingVerifyPlugin — 谛听辩论式验证插件
"""
from __future__ import annotations
import asyncio
import json
import logging
from typing import Optional

from plugins.base import BasePlugin

logger = logging.getLogger(__name__)


class DitingVerifyPlugin(BasePlugin):
    """谛听辩论式验证插件"""

    name = "diting_verify"
    description = "将根因报告和修复方案提交给谛听进行辩论式验证"
    phase = "post_execution"

    _api_url: str = "http://localhost:9090/verify"
    _timeout: int = 60
    _min_complexity: str = "moderate"

    async def init(self) -> None:
        logger.info(f"DitingVerifyPlugin initialized, api={self._api_url}")

    async def validate(self) -> bool:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(self._api_url.replace("/verify", "/health"))
                return resp.status_code == 200
        except Exception:
            logger.warning("Diting API not available")
            return False

    async def run(self, state: dict) -> dict:
        logger.info("DitingVerifyPlugin: verifying...")

        # 跳过简单任务
        complexity = state.get("task_complexity", state.get("complexity", "simple"))
        tiers = {"simple": 0, "moderate": 1, "complex": 2}
        if tiers.get(complexity, 0) < tiers.get(self._min_complexity, 1):
            state["diting_verdict"] = "SKIPPED"
            state["evidence_score"] = -1
            return state

        if not await self.validate():
            state["diting_verdict"] = "UNAVAILABLE"
            state["evidence_score"] = -1
            state["plugin_skipped"] = state.get("plugin_skipped", []) + ["diting_verify"]
            return state

        package = {
            "task": state.get("task", ""),
            "root_cause": self._extract_text(state, ["root_cause", "root_cause_analysis", "plan"]),
            "fix_proposal": self._extract_text(state, ["global_result", "final_output", "result"]),
            "code_context": self._extract_text(state, ["code_context", "code_content"]),
            "evidence": self._extract_evidence(state),
            "mode": "debate",
        }

        verdict, score, feedback = "REVISE", 50, ""
        for attempt in range(3):
            try:
                import httpx
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.post(self._api_url, json=package)
                    if resp.status_code == 200:
                        data = resp.json()
                        verdict = data.get("verdict", verdict)
                        score = data.get("evidence_score", score)
                        feedback = data.get("feedback", "")
                        break
            except Exception as e:
                logger.warning(f"Diting attempt {attempt+1} failed: {e}")
                if attempt < 2:
                    await asyncio.sleep(2 ** attempt)

        state["diting_verdict"] = verdict
        state["evidence_score"] = score
        state["diting_feedback"] = feedback
        logger.info(f"Diting verdict: {verdict} ({score}/100)")
        return state

    def _extract_text(self, state: dict, keys: list) -> str:
        for key in keys:
            if key in state and state[key]:
                v = state[key]
                return v[:2000] if isinstance(v, str) else str(v)[:2000]
        return ""

    def _extract_evidence(self, state: dict) -> list:
        evidence = []
        for key in ["evidence", "evidence_chain", "phases"]:
            if key in state and isinstance(state[key], list):
                evidence.extend(state[key][:5])
        return evidence[:10]