#!/usr/bin/env python3
"""
LightAgent - 编辑器 / 子任务执行

推荐模型: gpt-4o-mini / gemini-2.0-flash (tier 2)
职责: 执行子任务、生成代码、运行测试
"""
from __future__ import annotations
import logging
from agents.base_agent import BaseAgent
from workflows.task_workflow import Task
from core.agent_chain import _call_model

logger = logging.getLogger(__name__)


class LightAgent(BaseAgent):
    """Light Agent — 子任务执行"""

    def __init__(self, name: str = "Editor",
                 model_type: str = "light",
                 role: str = "editor"):
        super().__init__(name, model_type, role)
        self.system_prompt = (
            "You are a code editor. Execute the assigned task precisely. "
            "Generate clean, well-documented code. Run tests if needed."
        )

    async def execute_task(self, task: Task) -> Task:
        logger.info(f"LightAgent executing task: {task.task_id}")
        # Build context from task steps
        step_descriptions = "\n".join(
            f"- {s.description}" for s in task.steps
        )
        prompt = (
            f"Execute the following task:\n\n"
            f"Description: {task.description}\n"
            f"Steps to execute:\n{step_descriptions}\n\n"
            f"Provide the implementation."
        )
        try:
            result = await _call_model(prompt, system_prompt=self.system_prompt,
                                        model_name="gpt-4o-mini")
            task.result = (task.result or "") + f" | Executed: {result[:500]}"
        except Exception as e:
            logger.error(f"LightAgent failed: {e}")
            task.result = (task.result or "") + f" | ExecutionError: {e}"
        return task

    async def analyze(self, input_text: str) -> str:
        return await _call_model(
            f"Execute the following:\n\n{input_text}",
            system_prompt=self.system_prompt,
            model_name="gpt-4o-mini",
        )