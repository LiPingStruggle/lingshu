#!/usr/bin/env python3
"""
StrongAgent - 架构师 / Reviewer

推荐模型: gpt-4.1 / deepseek-chat (tier 4)
职责: 需求分析、任务分配、复核结果
"""
from __future__ import annotations
import logging
from agents.base_agent import BaseAgent
from workflows.task_workflow import Task
from core.agent_chain import _call_model

logger = logging.getLogger(__name__)


class StrongAgent(BaseAgent):
    """Strong Agent — 架构设计和质量复核"""

    def __init__(self, name: str = "Reviewer",
                 model_type: str = "strong",
                 role: str = "reviewer"):
        super().__init__(name, model_type, role)
        self.system_prompt = (
            "You are a senior architect and code reviewer. "
            "Review code changes for correctness, completeness, and quality. "
            "Provide structured feedback."
        )

    async def execute_task(self, task: Task) -> Task:
        logger.info(f"StrongAgent reviewing task: {task.task_id}")
        prompt = f"Review the following task result:\n\nTask: {task.description}\nResult: {task.result}"
        try:
            result = await _call_model(prompt, system_prompt=self.system_prompt,
                                        model_name="gpt-4.1")
            task.result = (task.result or "") + f" | Reviewed: {result[:300]}"
        except Exception as e:
            logger.error(f"StrongAgent failed: {e}")
        return task

    async def analyze(self, input_text: str) -> str:
        return await _call_model(
            f"Analyze the following:\n\n{input_text}",
            system_prompt=self.system_prompt,
            model_name="gpt-4.1",
        )

    async def review(self, task: Task) -> tuple[bool, str]:
        """复核任务，返回 (通过, 意见)"""
        prompt = (
            f"Review this task result. Is it correct and complete?\n\n"
            f"Task: {task.description}\n"
            f"Status: {task.status}\n"
            f"Result: {task.result}\n\n"
            f"Answer PASS or FAIL with a brief reason."
        )
        result = await _call_model(prompt, system_prompt=self.system_prompt,
                                    model_name="gpt-4.1")
        passed = "PASS" in result.upper() and "FAIL" not in result.upper()[:10]
        return passed, result