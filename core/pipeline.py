#!/usr/bin/env python3
"""
Pipeline - 任务流水线 + SQLite 检查点 + 断点恢复

职责:
- 管理 Task 的完整生命周期
- SQLite 持久化任务和检查点
- 支持断点恢复 resume
- 调用 AgentChain 分派和执行步骤
"""
from __future__ import annotations
import asyncio
import json
import sqlite3
import logging
import os
from datetime import datetime
from typing import List, Optional, Callable
from core.agent_chain import AgentChain
from core.inverse_verifier import InverseVerifier, InverseVerifierConfig
from workflows.task_workflow import Task, TaskStep

logger = logging.getLogger(__name__)


class Pipeline:
    """任务流水线"""

    def __init__(self, agent_chain: AgentChain, db_path: str = "lingshu.db",
                 inverse_verifier: Optional[InverseVerifier] = None):
        self.agent_chain = agent_chain
        self.inverse_verifier = inverse_verifier
        self.db_path = os.path.abspath(db_path)
        self._init_db()
        logger.info(f"Pipeline initialized, db={self.db_path}")

    def _init_db(self) -> None:
        """初始化数据库表"""
        os.makedirs(os.path.dirname(self.db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        try:
            c = conn.cursor()
            c.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                description TEXT,
                type TEXT DEFAULT 'generic',
                dependencies TEXT DEFAULT '[]',
                status TEXT DEFAULT 'pending',
                result TEXT,
                error TEXT,
                created_at TEXT,
                updated_at TEXT
            )
            """)
            c.execute("""
            CREATE TABLE IF NOT EXISTS task_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                step_id TEXT NOT NULL,
                description TEXT,
                assigned_agent TEXT,
                status TEXT DEFAULT 'pending',
                result TEXT,
                error TEXT,
                FOREIGN KEY (task_id) REFERENCES tasks(task_id)
            )
            """)
            c.execute("""
            CREATE TABLE IF NOT EXISTS checkpoints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                step_id TEXT,
                phase TEXT,
                status TEXT,
                output TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
            """)
            conn.commit()
        finally:
            conn.close()

    def add_task(self, task: Task) -> None:
        """添加任务到数据库"""
        now = datetime.now().isoformat()
        task.created_at = task.created_at or now
        task.updated_at = now

        conn = sqlite3.connect(self.db_path)
        try:
            c = conn.cursor()
            c.execute(
                "INSERT OR REPLACE INTO tasks VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    task.task_id,
                    task.description,
                    task.type,
                    json.dumps(task.dependencies),
                    task.status,
                    task.result,
                    task.error,
                    task.created_at,
                    task.updated_at,
                ),
            )
            for step in task.steps:
                c.execute(
                    "INSERT OR REPLACE INTO task_steps "
                    "(task_id, step_id, description, assigned_agent, status, result, error) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (task.task_id, step.step_id, step.description,
                     step.assigned_agent, step.status, step.result, step.error),
                )
            conn.commit()
        finally:
            conn.close()
        logger.info(f"Task {task.task_id} saved to database")

    def update_task(self, task: Task) -> None:
        """更新任务状态"""
        task.updated_at = datetime.now().isoformat()
        conn = sqlite3.connect(self.db_path)
        try:
            c = conn.cursor()
            c.execute(
                "UPDATE tasks SET status=?, result=?, error=?, updated_at=? WHERE task_id=?",
                (task.status, task.result, task.error, task.updated_at, task.task_id),
            )
            for step in task.steps:
                c.execute(
                    "UPDATE task_steps SET status=?, result=?, error=? "
                    "WHERE task_id=? AND step_id=?",
                    (step.status, step.result, step.error, task.task_id, step.step_id),
                )
            conn.commit()
        finally:
            conn.close()

    def get_task(self, task_id: str) -> Optional[Task]:
        """从数据库加载任务"""
        conn = sqlite3.connect(self.db_path)
        try:
            c = conn.cursor()
            c.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,))
            row = c.fetchone()
            if not row:
                return None

            task = Task(
                task_id=row[0],
                description=row[1],
                type=row[2],
                dependencies=json.loads(row[3]),
                status=row[4],
                result=row[5],
                error=row[6],
            )
            task.created_at = row[7]
            task.updated_at = row[8]

            # 加载步骤
            c.execute(
                "SELECT step_id, description, assigned_agent, status, result, error "
                "FROM task_steps WHERE task_id=? ORDER BY id",
                (task_id,),
            )
            for srow in c.fetchall():
                task.steps.append(TaskStep(
                    step_id=srow[0],
                    description=srow[1],
                    assigned_agent=srow[2],
                    status=srow[3],
                    result=srow[4],
                    error=srow[5],
                ))
            return task
        finally:
            conn.close()

    def save_checkpoint(self, task_id: str, step_id: Optional[str],
                        phase: str, status: str, output: str = "") -> None:
        """保存检查点"""
        conn = sqlite3.connect(self.db_path)
        try:
            c = conn.cursor()
            c.execute(
                "INSERT INTO checkpoints (task_id, step_id, phase, status, output) "
                "VALUES (?, ?, ?, ?, ?)",
                (task_id, step_id, phase, status, output),
            )
            conn.commit()
        finally:
            conn.close()

    def get_checkpoints(self, task_id: str) -> List[dict]:
        """获取任务的所有检查点"""
        conn = sqlite3.connect(self.db_path)
        try:
            c = conn.cursor()
            c.execute(
                "SELECT step_id, phase, status, created_at FROM checkpoints "
                "WHERE task_id=? ORDER BY id",
                (task_id,),
            )
            return [
                {"step_id": r[0], "phase": r[1], "status": r[2], "created_at": r[3]}
                for r in c.fetchall()
            ]
        finally:
            conn.close()

    async def run_task(self, task: Task,
                       on_progress: Optional[Callable] = None) -> Task:
        """执行单个任务"""
        logger.info(f"Running task {task.task_id}: {task.description}")
        task.status = "running"
        self.update_task(task)
        self.save_checkpoint(task.task_id, None, "start", "running")

        if on_progress:
            on_progress(task)

        # 1. AgentChain 分派执行
        task = await self.agent_chain.dispatch_task(task)
        self.save_checkpoint(task.task_id, None, "dispatch", task.status)

        if task.status == "failed":
            self.update_task(task)
            self.save_checkpoint(task.task_id, None, "failed", "failed", task.error or "")
            return task

        # 2. Strong Agent 复核
        if task.status == "done":
            reviewed = await self.agent_chain.review_task(task)
            self.save_checkpoint(task.task_id, None, "review",
                                 "passed" if reviewed else "failed")

            if not reviewed:
                task.status = "failed"
                task.error = "Review failed: result did not pass quality check"
                self.update_task(task)
                return task

            # 3. 反向验证器对抗验证
            if self.inverse_verifier and self.inverse_verifier.config.enabled:
                verify_result = await self.inverse_verifier.verify(task)
                self.save_checkpoint(
                    task.task_id, None, "adversarial_verify",
                    "passed" if verify_result.final_passed else "failed",
                    json.dumps(verify_result.to_dict(), ensure_ascii=False)
                )
                if not verify_result.final_passed:
                    task.status = "failed"
                    task.error = (
                        f"Adversarial verification failed: "
                        f"confidence={verify_result.final_confidence:.1f}%, "
                        f"rounds={verify_result.total_rounds}"
                    )
                    self.update_task(task)
                    return task
                logger.info(
                    f"Task {task.task_id} passed adversarial verification "
                    f"(confidence={verify_result.final_confidence:.1f}%)"
                )

        self.update_task(task)
        logger.info(f"Task {task.task_id} completed with status: {task.status}")

        if on_progress:
            on_progress(task)
        return task

    async def run_tasks_batch(self, tasks: List[Task],
                              on_progress: Optional[Callable] = None) -> List[Task]:
        """批量执行任务（支持依赖排序）"""
        # 拓扑排序
        ordered = self._topological_sort(tasks)
        results = []

        for task in ordered:
            result = await self.run_task(task, on_progress)
            results.append(result)

        return results

    async def resume(self, task_id: str,
                     on_progress: Optional[Callable] = None) -> Optional[Task]:
        """从检查点恢复任务"""
        task = self.get_task(task_id)
        if not task:
            logger.error(f"Task {task_id} not found")
            return None

        if task.status == "done":
            logger.info(f"Task {task_id} already completed")
            return task

        # 从失败的步骤恢复
        for i, step in enumerate(task.steps):
            if step.status == "failed":
                logger.info(f"Resuming task {task_id} from step {step.step_id}")
                # 重新执行失败步骤及后续
                remaining = task.steps[i:]
                for s in remaining:
                    s.status = "pending"
                    s.result = None
                    s.error = None

                task.status = "running"
                self.update_task(task)
                self.save_checkpoint(task_id, step.step_id, "resume", "running")

                return await self.run_task(task, on_progress)

        logger.warning(f"Task {task_id} has no failed steps, cannot resume")
        return task

    def list_tasks(self, status: Optional[str] = None) -> List[dict]:
        """列出所有任务"""
        conn = sqlite3.connect(self.db_path)
        try:
            c = conn.cursor()
            if status:
                c.execute(
                    "SELECT task_id, description, type, status, created_at "
                    "FROM tasks WHERE status=? ORDER BY created_at DESC",
                    (status,),
                )
            else:
                c.execute(
                    "SELECT task_id, description, type, status, created_at "
                    "FROM tasks ORDER BY created_at DESC"
                )
            return [
                {
                    "task_id": r[0],
                    "description": r[1],
                    "type": r[2],
                    "status": r[3],
                    "created_at": r[4],
                }
                for r in c.fetchall()
            ]
        finally:
            conn.close()

    @staticmethod
    def _topological_sort(tasks: List[Task]) -> List[Task]:
        """拓扑排序，保证依赖先执行"""
        task_map = {t.task_id: t for t in tasks}
        visited = set()
        result = []

        def dfs(task_id: str) -> None:
            if task_id in visited:
                return
            visited.add(task_id)
            task = task_map.get(task_id)
            if task:
                for dep in task.dependencies:
                    if dep in task_map:
                        dfs(dep)
                result.append(task)

        for t in tasks:
            dfs(t.task_id)
        return result