#!/usr/bin/env python3
"""
ExecutionModes - 6 种执行模式

来源（第 4 章 REQUIREMENTS.md）:
task, analysis, auto-approve, manual, cascade, debate
"""
from __future__ import annotations
import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class ExecutionConfig:
    """执行模式配置"""
    mode_id: str
    auto_approve: bool = True
    stop_on_error: bool = False
    max_retries: int = 2
    max_phases: int = 5
    pause_on_each_task: bool = False
    auto_escalate: bool = False
    require_user_approval: bool = False
    max_iterations: int = 3


EXECUTION_MODES: dict[str, ExecutionConfig] = {
    "task": ExecutionConfig(
        mode_id="task",
        auto_approve=True,
        stop_on_error=False,
        max_retries=2,
        max_phases=5,
    ),
    "analysis": ExecutionConfig(
        mode_id="analysis",
        auto_approve=False,
        stop_on_error=True,
        max_phases=2,
    ),
    "auto-approve": ExecutionConfig(
        mode_id="auto-approve",
        auto_approve=True,
        stop_on_error=False,
        max_retries=3,
        auto_escalate=False,
    ),
    "manual": ExecutionConfig(
        mode_id="manual",
        auto_approve=False,
        stop_on_error=True,
        pause_on_each_task=True,
        require_user_approval=True,
    ),
    "cascade": ExecutionConfig(
        mode_id="cascade",
        auto_approve=True,
        stop_on_error=False,
        max_retries=2,
        max_iterations=3,
    ),
    "debate": ExecutionConfig(
        mode_id="debate",
        auto_approve=False,
        stop_on_error=True,
        max_iterations=3,
        require_user_approval=True,
    ),
}


async def execute_with_mode(
    mode_id: str,
    orchestrator,
    user_input: str,
    on_progress: Optional[Callable] = None,
) -> dict:
    """按指定执行模式执行"""
    config = EXECUTION_MODES.get(mode_id)
    if not config:
        raise ValueError(f"Unknown execution mode: {mode_id}")

    logger.info(f"Executing with mode: {mode_id}")
    logger.info(f"Config: auto_approve={config.auto_approve}, "
                f"stop_on_error={config.stop_on_error}, "
                f"max_retries={config.max_retries}")

    return await orchestrator.start_run(
        user_input=user_input,
        mode=mode_id,
        on_progress=on_progress,
        yes=config.auto_approve,
    )


def get_mode_config(mode_id: str) -> Optional[ExecutionConfig]:
    return EXECUTION_MODES.get(mode_id)


def list_execution_modes() -> list[dict]:
    return [{
        "id": k,
        "auto_approve": v.auto_approve,
        "stop_on_error": v.stop_on_error,
        "max_retries": v.max_retries,
        "pause_on_each_task": v.pause_on_each_task,
    } for k, v in EXECUTION_MODES.items()]