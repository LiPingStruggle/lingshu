#!/usr/bin/env python3
"""
BaseAgent - Agent 基类
所有 Agent 继承此类
"""
from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Optional
from workflows.task_workflow import Task


class BaseAgent(ABC):
    """Agent 基类"""

    def __init__(self, name: str, model_type: str, role: str):
        self.name = name
        self.model_type = model_type
        self.role = role

    @abstractmethod
    async def execute_task(self, task: Task) -> Task:
        """执行任务"""
        ...

    @abstractmethod
    async def analyze(self, input_text: str) -> str:
        """分析输入"""
        ...