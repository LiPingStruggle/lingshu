#!/usr/bin/env python3
"""
EliteAgent - 根因分析 / 高级决策

推荐模型: claude-sonnet-4 (tier 5)
职责: 深度分析 bug 根因、复杂推理、任务拆解
"""
from __future__ import annotations
import logging
from agents.base_agent import BaseAgent
from workflows.task_workflow import Task, TaskStep
from core.agent_chain import _call_model

logger = logging.getLogger(__name__)


class EliteAgent(BaseAgent):
    """精英 Agent — 根因分析和任务拆解"""

    def __init__(self, name: str = "RootCause",
                 model_type: str = "elite",
                 role: str = "root_cause"):
        super().__init__(name, model_type, role)
        self.system_prompt = (
            "You are an elite root cause analyst. "
            "Given a problem description, identify the root cause, "
            "analyze the call chain, and provide a detailed breakdown. "
            "Output in a structured format."
        )

    async def execute_task(self, task: Task) -> Task:
        logger.info(f"EliteAgent analyzing task: {task.task_id}")
        prompt = f"Analyze the following task and identify root causes:\n\n{task.description}"
        try:
            result = await _call_model(prompt, system_prompt=self.system_prompt,
                                        model_name="claude-sonnet-4")
            task.result = (task.result or "") + f" | RootCause: {result[:500]}"
        except Exception as e:
            logger.error(f"EliteAgent failed: {e}")
            task.result = (task.result or "") + f" | RootCauseError: {e}"
        return task

    async def analyze(self, input_text: str) -> str:
        return await _call_model(
            f"Perform deep analysis on the following:\n\n{input_text}",
            system_prompt=self.system_prompt,
            model_name="claude-sonnet-4",
        )

    async def decompose(self, description: str) -> list[TaskStep]:
        """拆解任务为子步骤"""
        prompt = (
            f"Decompose the following task into steps. "
            f"For each step, specify which agent should handle it "
            f"(elite for analysis, light for execution, strong for review).\n\n"
            f"Task: {description}"
        )
        result = await _call_model(prompt, system_prompt=self.system_prompt,
                                    model_name="claude-sonnet-4")
        # Parse structured output into steps (simplified)
        steps = [
            TaskStep(step_id="S001", description=description, assigned_agent="light"),
            TaskStep(step_id="S002", description=f"Review: {description}", assigned_agent="strong"),
        ]
        return steps