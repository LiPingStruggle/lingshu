"""
Task 基础数据模型
所有模块依赖此模块定义的 Task/Step 类
"""
from __future__ import annotations
from typing import List, Optional, Any
from dataclasses import dataclass, field


@dataclass
class TaskStep:
    """任务步骤，由 Elite Agent 拆解生成"""
    step_id: str
    description: str
    assigned_agent: str  # 'elite' | 'strong' | 'light'
    status: str = "pending"  # pending | running | done | failed
    result: Optional[str] = None
    error: Optional[str] = None


@dataclass
class Task:
    """任务对象，是流水线的最小执行单位"""
    task_id: str
    description: str
    type: str = "generic"  # bug_fix | refactor | test_generation | generic
    steps: List[TaskStep] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)  # 依赖的任务 ID 列表
    status: str = "pending"  # pending | running | done | failed
    result: Optional[str] = None
    error: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None