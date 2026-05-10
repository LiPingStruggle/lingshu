#!/usr/bin/env python3
"""
Batch Runner - 批量执行任务库

功能:
- 从 tasks/ 目录加载所有任务
- 按依赖拓扑排序
- 批量执行 + 详细日志
- 支持中断恢复
"""
from __future__ import annotations
import asyncio
import json
import logging
import sys
import os
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from core.engine import Engine
from core.pipeline import Pipeline
from core.agent_chain import AgentChain, Agent
from core.model_router import ModelRouter
from core.error_recovery import ErrorRecovery
from core.resource_monitor import ResourceMonitor

logger = logging.getLogger(__name__)


class BatchRunner:
    """批量任务执行器"""

    def __init__(
        self,
        tasks_dir: str = "tasks",
        db_path: str = "lingshu.db",
        max_concurrent: int = 3,
        max_duration_hours: int = 10,
    ):
        self.tasks_dir = tasks_dir
        self.db_path = db_path
        self.max_concurrent = max_concurrent
        self.max_duration_hours = max_duration_hours

        # 初始化子系统
        self.model_router = ModelRouter()
        self.model_router.register_default_models()

        self.resource_monitor = ResourceMonitor(max_concurrent=max_concurrent)
        self.error_recovery = ErrorRecovery(model_router=self.model_router)
        self.agent_chain = AgentChain()
        self.pipeline = Pipeline(
            agent_chain=self.agent_chain,
            db_path=self.db_path,
        )
        self.engine = Engine(
            pipeline=self.pipeline,
            agent_chain=self.agent_chain,
            error_recovery=self.error_recovery,
            resource_monitor=self.resource_monitor,
            tasks_dir=self.tasks_dir,
            max_duration_hours=self.max_duration_hours,
        )

        # 注册回调
        self.engine.on_task_complete(self._on_complete)
        self.engine.on_task_fail(self._on_fail)

    def _on_complete(self, task) -> None:
        logger.info(f"[BATCH] Task {task.task_id} completed: {task.result[:80] if task.result else 'N/A'}...")

    def _on_fail(self, task_id: str) -> None:
        logger.error(f"[BATCH] Task {task_id} failed")

    async def run_all(self) -> dict:
        """运行所有任务"""
        logger.info("=" * 60)
        logger.info("BATCH RUNNER STARTED")
        logger.info(f"Tasks dir: {self.tasks_dir}")
        logger.info(f"Max duration: {self.max_duration_hours}h")
        logger.info(f"Max concurrent: {self.max_concurrent}")
        logger.info("=" * 60)

        stats = await self.engine.start()

        logger.info("=" * 60)
        logger.info("BATCH RUNNER FINISHED")
        logger.info(f"Completed: {stats['completed']}")
        logger.info(f"Failed: {stats['failed']}")
        logger.info(f"Retries: {stats['retries']}")
        logger.info(f"Loop count: {stats['loop_count']}")
        logger.info(f"Elapsed: {stats.get('elapsed', 'N/A')}")
        logger.info(f"Status: {stats['status']}")
        logger.info("=" * 60)

        return stats

    async def run_single(self, task_file: str) -> dict:
        """运行单个任务文件"""
        logger.info(f"Running single task file: {task_file}")

        with open(task_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        from workflows.task_workflow import Task, TaskStep
        task = Task(
            task_id=data["task_id"],
            description=data["description"],
            type=data.get("type", "generic"),
            dependencies=data.get("dependencies", []),
        )
        for s in data.get("steps", []):
            task.steps.append(TaskStep(
                step_id=s["step_id"],
                description=s["description"],
                assigned_agent=s["assigned_agent"],
            ))

        self.pipeline.add_task(task)
        result = await self.pipeline.run_task(task)

        return {
            "task_id": result.task_id,
            "status": result.status,
            "result": result.result,
            "error": result.error,
        }


async def main():
    """CLI 入口"""
    import argparse

    parser = argparse.ArgumentParser(description="Batch Runner")
    parser.add_argument(
        "command",
        nargs="?",
        choices=["run", "single", "resume", "status"],
        default="run",
        help="Command to execute",
    )
    parser.add_argument("--task-file", "-f", help="Single task file path")
    parser.add_argument(
        "--tasks-dir", "-d", default="tasks", help="Tasks directory"
    )
    parser.add_argument(
        "--db", default="lingshu.db", help="SQLite database path"
    )
    parser.add_argument(
        "--max-hours", type=int, default=10, help="Max duration in hours"
    )
    parser.add_argument(
        "--max-concurrent", type=int, default=3, help="Max concurrent tasks"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Verbose output"
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )

    runner = BatchRunner(
        tasks_dir=args.tasks_dir,
        db_path=args.db,
        max_concurrent=args.max_concurrent,
        max_duration_hours=args.max_hours,
    )

    if args.command == "single":
        if not args.task_file:
            print("Error: --task-file required for 'single' command")
            sys.exit(1)
        result = await runner.run_single(args.task_file)
        print(json.dumps(result, indent=2, ensure_ascii=False))

    elif args.command == "status":
        print(json.dumps(runner.engine.progress, indent=2))

    elif args.command == "resume":
        logger.info("Resuming from checkpoint...")
        stats = await runner.run_all()
        print(json.dumps(stats, indent=2, ensure_ascii=False, default=str))

    else:
        stats = await runner.run_all()
        print(json.dumps(stats, indent=2, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(main())