#!/usr/bin/env python3
"""
Engine - 持续运行循环引擎（硬性核心）

核心行为:
1. 循环扫描 tasks/ 目录下所有 JSON 任务文件
2. 逐个执行任务（Elite 分析 → Light 执行 → Strong 复核）
3. 失败/断开自动重试（指数退避）
4. 持续运行直到所有任务完成或超时
5. 支持中断恢复（检查点机制）
6. 10 小时定时任务支持
7. 实时心跳日志，证明引擎存活
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import signal
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Optional, Set, Callable, Awaitable

from core.agent_chain import AgentChain
from core.pipeline import Pipeline
from core.model_router import ModelRouter
from core.error_recovery import ErrorRecovery
from core.resource_monitor import ResourceMonitor
from core.inverse_verifier import InverseVerifier
from workflows.task_workflow import Task, TaskStep

logger = logging.getLogger(__name__)


class Engine:
    """
    持续运行引擎 - 任务的循环执行核心

    这是整个系统的**硬性核心需求**:
    - 永远不应该在不运行的状态
    - 循环读取任务清单，持续处理
    - 失败自动重试，断开自动恢复
    - 10 小时不间断运行
    """

    def __init__(
        self,
        pipeline: Pipeline,
        agent_chain: AgentChain,
        error_recovery: ErrorRecovery,
        resource_monitor: ResourceMonitor,
        inverse_verifier: Optional[InverseVerifier] = None,
        tasks_dir: str = "tasks",
        max_duration_hours: int = 10,
        retry_delay_base: float = 5.0,
        max_retries_per_task: int = 3,
        heartbeat_interval: int = 60,
        scan_interval: int = 15,
        exit_when_done: bool = False,
    ):
        self.pipeline = pipeline
        self.agent_chain = agent_chain
        self.error_recovery = error_recovery
        self.resource_monitor = resource_monitor
        self.inverse_verifier = inverse_verifier
        self.tasks_dir = os.path.abspath(tasks_dir)
        self.max_duration = timedelta(hours=max_duration_hours)
        self.retry_delay_base = retry_delay_base
        self.max_retries_per_task = max_retries_per_task
        self.heartbeat_interval = heartbeat_interval
        self.scan_interval = scan_interval
        self.exit_when_done = exit_when_done

        self._running = False
        self._completed_tasks: Set[str] = set()
        self._failed_tasks: Set[str] = set()
        self._in_progress: Set[str] = set()
        self._start_time: Optional[datetime] = None
        self._on_task_complete: Optional[Callable] = None
        self._on_task_fail: Optional[Callable] = None
        self._shutdown_requested = False
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._loop_count = 0

        # 注册信号处理
        self._setup_signal_handlers()

    def _setup_signal_handlers(self) -> None:
        """注册优雅关闭的信号处理"""
        try:
            loop = asyncio.get_event_loop()
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(
                    sig,
                    lambda s=sig: asyncio.create_task(self._handle_shutdown(s))
                )
        except (NotImplementedError, RuntimeError):
            pass  # Windows 不支持 add_signal_handler

    async def _handle_shutdown(self, sig: int) -> None:
        """优雅关闭处理"""
        sig_name = signal.Signals(sig).name
        logger.warning(f"Received {sig_name}, shutting down gracefully...")
        self._shutdown_requested = True
        self._running = False
        # 立即保存检查点
        self._save_checkpoint()
        logger.info("Checkpoint saved on shutdown")

    def on_task_complete(self, callback: Callable) -> None:
        self._on_task_complete = callback

    def on_task_fail(self, callback: Callable) -> None:
        self._on_task_fail = callback

    async def start(self) -> dict:
        """
        启动持续运行循环

        这是引擎的入口点。它会:
        1. 加载检查点恢复上次状态
        2. 进入主循环扫描 tasks/ 目录
        3. 持续执行直到完成或超时
        4. 返回执行统计
        """
        self._running = True
        self._start_time = datetime.now()
        self._shutdown_requested = False
        self.end_time = self._start_time + self.max_duration

        # 加载检查点
        self._load_checkpoint()

        # 启动心跳
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        logger.info("=" * 60)
        logger.info(f"ENGINE STARTED at {self._start_time}")
        logger.info(f"Tasks directory: {self.tasks_dir}")
        if self.max_duration.total_seconds() > 0:
            logger.info(f"Max duration: {self.max_duration} (until {self.end_time})")
        else:
            logger.info(f"Mode: CONTINUOUS (no time limit)")
        logger.info(f"Max retries per task: {self.max_retries_per_task}")
        logger.info(f"Completed from checkpoint: {len(self._completed_tasks)}")
        logger.info(f"Failed from checkpoint: {len(self._failed_tasks)}")
        if self.inverse_verifier:
            ts = self.inverse_verifier.trust_statistics
            logger.info(
                f"Verifier trust: {ts['consecutive_pass']}/{ts['required']} "
                f"(trusted={ts['trusted']}, pass_rate={ts['pass_rate']}%)"
            )
        logger.info("=" * 60)

        stats = {
            "started_at": self._start_time.isoformat(),
            "end_at": self.end_time.isoformat(),
            "total_tasks_discovered": 0,
            "completed": len(self._completed_tasks),
            "failed": len(self._failed_tasks),
            "retries": 0,
            "loop_count": 0,
            "errors": [],
            "status": "running",
        }

        try:
            while self._running:
                self._loop_count += 1
                stats["loop_count"] = self._loop_count

                if self._shutdown_requested:
                    logger.info("Shutdown requested, breaking main loop")
                    break

                # === 检查是否达到时间上限 ===
                if self.max_duration.total_seconds() > 0 and datetime.now() >= self.end_time:
                    logger.info(f"Max duration {self.max_duration} reached, stopping")
                    break

                # === 循环扫描任务清单 ===
                task_files = self._scan_task_files()
                stats["total_tasks_discovered"] = len(task_files)

                # 检测信任状态
                trust_skipped = False
                if self.inverse_verifier and self.inverse_verifier.is_environment_trusted:
                    trust_info = self.inverse_verifier.trust_statistics
                    logger.info(
                        f"[Loop {self._loop_count}] Environment TRUSTED "
                        f"(consecutive_pass={trust_info['consecutive_pass']}/"
                        f"{trust_info['required']})"
                    )
                    if not task_files:
                        # 信任环境 + 无任务 = 环境验证通过, 引擎可持续等待新任务
                        logger.info(
                            f"[TRUST] Trusted environment verified. "
                            f"Scanning for new tasks..."
                        )
                        trust_skipped = True

                if not trust_skipped and not task_files:
                    logger.info(
                        f"[Loop {self._loop_count}] No task files found. "
                        f"Waiting {self.scan_interval}s before next scan..."
                    )
                    await asyncio.sleep(self.scan_interval)
                    continue

                # 过滤出待处理的任务
                pending_files = self._filter_pending(task_files)

                if not pending_files:
                    logger.info(
                        f"[Loop {self._loop_count}] All {len(task_files)} task(s) processed. "
                        f"Waiting for new tasks..."
                    )
                    await asyncio.sleep(self.scan_interval * 2)
                    continue

                logger.info(
                    f"[Loop {self._loop_count}] Found {len(pending_files)} pending task(s) "
                    f"out of {len(task_files)} total"
                )

                # 处理待执行的任务
                for file_path in pending_files:
                    if self._shutdown_requested:
                        break
                    if self.max_duration.total_seconds() > 0 and datetime.now() >= self.end_time:
                        break
                    await self._process_task_file(file_path, stats)

                # 短暂暂停后再次扫描
                await asyncio.sleep(self.scan_interval)

        except asyncio.CancelledError:
            logger.warning("Engine task cancelled")
            stats["status"] = "cancelled"
        except Exception as e:
            logger.error(f"Engine fatal error: {e}")
            logger.error(traceback.format_exc())
            stats["errors"].append(f"Fatal: {e}")
            stats["status"] = "crashed"
        finally:
            self._running = False
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
            self._save_checkpoint()

            elapsed = datetime.now() - self._start_time
            logger.info("=" * 60)
            logger.info(f"ENGINE STOPPED after {elapsed}")
            logger.info(f"Completed: {stats['completed']}, Failed: {stats['failed']}")
            logger.info(f"Loop count: {stats['loop_count']}")
            logger.info(f"Status: {stats['status']}")
            logger.info("=" * 60)

            stats["elapsed"] = str(elapsed)
            stats["end_time"] = datetime.now().isoformat()
            return stats

    def stop(self) -> None:
        """请求停止引擎（幂等安全）"""
        logger.info("Stop requested")
        self._shutdown_requested = True
        self._running = False

    async def _heartbeat_loop(self) -> None:
        """心跳循环，证明引擎存活"""
        while self._running and not self._shutdown_requested:
            elapsed = datetime.now() - self._start_time if self._start_time else timedelta()
            remaining = self.max_duration - elapsed if self._start_time else self.max_duration
            trust_str = ""
            if self.inverse_verifier:
                ts = self.inverse_verifier.trust_statistics
                trust_str = f", trust={ts['consecutive_pass']}/{ts['required']}(trusted={ts['trusted']})"
            logger.info(
                f"[HEARTBEAT] Running for {elapsed}, "
                f"remaining ~{remaining}, "
                f"completed={len(self._completed_tasks)}, "
                f"failed={len(self._failed_tasks)}, "
                f"in_progress={len(self._in_progress)}, "
                f"loops={self._loop_count}{trust_str}"
            )
            try:
                await asyncio.sleep(self.heartbeat_interval)
            except asyncio.CancelledError:
                break

    def _scan_task_files(self) -> List[str]:
        """
        扫描 tasks/ 目录下所有 JSON 任务文件
        返回按路径排序的列表以保持执行顺序一致性
        """
        task_files = []
        if not os.path.exists(self.tasks_dir):
            logger.warning(f"Tasks directory '{self.tasks_dir}' does not exist")
            return task_files

        try:
            for root, _, files in os.walk(self.tasks_dir):
                for f in sorted(files):
                    if f.endswith(".json") and not f.startswith("_"):
                        task_files.append(os.path.join(root, f))
        except Exception as e:
            logger.error(f"Error scanning task files: {e}")

        return task_files

    def _filter_pending(self, task_files: List[str]) -> List[str]:
        """过滤出待处理（未完成、未失败）的任务文件"""
        pending = []
        for fp in task_files:
            task_id = self._task_id_from_path(fp)
            if task_id in self._completed_tasks:
                continue
            if task_id in self._failed_tasks:
                continue
            if task_id in self._in_progress:
                continue
            pending.append(fp)
        return pending

    def _task_id_from_path(self, file_path: str) -> str:
        """从文件路径提取 task_id"""
        return os.path.splitext(os.path.basename(file_path))[0]

    def _load_checkpoint(self) -> None:
        """
        从磁盘加载检查点，恢复已完成/失败任务记录
        保证断开后能恢复进度
        """
        checkpoint_dir = os.path.join(os.path.dirname(self.tasks_dir) or ".", ".lingshu")
        checkpoint_file = os.path.join(checkpoint_dir, "engine_checkpoint.json")

        if os.path.exists(checkpoint_file):
            try:
                with open(checkpoint_file, "r") as f:
                    data = json.load(f)
                self._completed_tasks = set(data.get("completed", []))
                self._failed_tasks = set(data.get("failed", []))
                self._loop_count = data.get("loop_count", 0)
                logger.info(
                    f"Checkpoint loaded: {len(self._completed_tasks)} completed, "
                    f"{len(self._failed_tasks)} failed, "
                    f"loop #{self._loop_count}"
                )
            except Exception as e:
                logger.warning(f"Failed to load checkpoint (starting fresh): {e}")
                self._completed_tasks = set()
                self._failed_tasks = set()
        else:
            logger.info("No checkpoint found, starting fresh")

    def _save_checkpoint(self) -> None:
        """保存检查点到磁盘（每次任务完成后调用）"""
        checkpoint_dir = os.path.join(os.path.dirname(self.tasks_dir) or ".", ".lingshu")
        checkpoint_file = os.path.join(checkpoint_dir, "engine_checkpoint.json")

        try:
            os.makedirs(checkpoint_dir, exist_ok=True)
            with open(checkpoint_file, "w") as f:
                json.dump(
                    {
                        "completed": sorted(list(self._completed_tasks)),
                        "failed": sorted(list(self._failed_tasks)),
                        "loop_count": self._loop_count,
                        "updated_at": datetime.now().isoformat(),
                    },
                    f,
                    indent=2,
                    ensure_ascii=False,
                )
            logger.debug(f"Checkpoint saved ({len(self._completed_tasks)} completed)")
        except Exception as e:
            logger.warning(f"Failed to save checkpoint: {e}")

    async def _process_task_file(self, file_path: str, stats: dict) -> None:
        """处理单个任务文件（带完整重试逻辑）"""
        task_id = self._task_id_from_path(file_path)

        # 再次检查防止竞态
        if task_id in self._completed_tasks or task_id in self._failed_tasks:
            return

        self._in_progress.add(task_id)
        logger.info(f"▶ Processing task: {task_id} ({os.path.relpath(file_path)})")

        # 加载任务 JSON
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            logger.error(f"✗ Failed to load task file {file_path}: {e}")
            self._failed_tasks.add(task_id)
            self._in_progress.discard(task_id)
            stats["failed"] += 1
            self._save_checkpoint()
            return
        except Exception as e:
            logger.error(f"✗ Unexpected error loading {file_path}: {e}")
            self._failed_tasks.add(task_id)
            self._in_progress.discard(task_id)
            stats["failed"] += 1
            self._save_checkpoint()
            return

        # 转换为 Task 对象
        task = self._json_to_task(data)
        if task is None:
            logger.error(f"✗ Invalid task format in {file_path}")
            self._failed_tasks.add(task_id)
            self._in_progress.discard(task_id)
            stats["failed"] += 1
            self._save_checkpoint()
            return

        # === 带重试的执行循环 ===
        retry_count = 0
        last_error = None

        while retry_count <= self.max_retries_per_task:
            if self._shutdown_requested:
                self._in_progress.discard(task_id)
                return

            if retry_count > 0:
                delay = self.retry_delay_base * (2 ** (retry_count - 1)) + (
                    hash(task_id) % 3
                )  # 基础退避 + 抖动
                logger.info(
                    f"⏳ Retry {retry_count}/{self.max_retries_per_task} "
                    f"for {task_id} in {delay:.0f}s..."
                )
                stats["retries"] += 1
                try:
                    await asyncio.sleep(delay)
                except asyncio.CancelledError:
                    self._in_progress.discard(task_id)
                    return

            try:
                # 检查资源
                if not self.resource_monitor.can_execute():
                    logger.debug("Waiting for resource slot...")
                    await asyncio.sleep(2)
                    continue

                # 先 add_task 确保 DB 中有记录
                self.pipeline.add_task(task)

                # 执行任务
                self.resource_monitor.track_task(task.task_id)
                result = await self.pipeline.run_task(task)
                self.resource_monitor.untrack_task(task.task_id)

                if result.status == "done":
                    self._completed_tasks.add(task_id)
                    self._in_progress.discard(task_id)
                    stats["completed"] += 1
                    self._save_checkpoint()

                    elapsed = datetime.now() - self._start_time if self._start_time else timedelta()
                    logger.info(
                        f"✓ Task {task_id} COMPLETED "
                        f"(loop #{self._loop_count}, elapsed {elapsed})"
                    )
                    if self._on_task_complete:
                        try:
                            self._on_task_complete(result)
                        except Exception:
                            pass
                    return

                # 执行完成但状态不是 done（pipeline 内部判定失败）
                last_error = result.error or "Unknown pipeline error"
                logger.warning(
                    f"⚠ Task {task_id} result status='{result.status}': {last_error}"
                )

            except asyncio.CancelledError:
                self._in_progress.discard(task_id)
                logger.warning(f"Task {task_id} cancelled")
                return

            except Exception as e:
                last_error = str(e)
                logger.error(
                    f"⚠ Task {task_id} exception (attempt {retry_count + 1}): {e}"
                )

                # 尝试错误恢复
                try:
                    recovered = await self.error_recovery.recover(
                        task=task,
                        error=last_error,
                        attempt=retry_count + 1,
                    )
                    if not recovered:
                        logger.error(f"✗ Error recovery failed for {task_id}")
                except Exception as recovery_err:
                    logger.error(f"✗ Error recovery itself failed: {recovery_err}")

            retry_count += 1

        # === 超出最大重试次数 ===
        self._failed_tasks.add(task_id)
        self._in_progress.discard(task_id)
        stats["failed"] += 1
        self._save_checkpoint()

        logger.error(
            f"✗ Task {task_id} FAILED after {self.max_retries_per_task} retries. "
            f"Last error: {last_error}"
        )
        if last_error:
            stats["errors"].append(f"{task_id}: {last_error}")
        if self._on_task_fail:
            try:
                self._on_task_fail(task_id)
            except Exception:
                pass

    def _json_to_task(self, data: dict) -> Optional[Task]:
        """将 JSON 数据转换为 Task 对象"""
        try:
            task = Task(
                task_id=data.get("task_id", "unknown"),
                description=data.get("description", ""),
                type=data.get("type", "generic"),
                dependencies=data.get("dependencies", []),
            )
            for step_data in data.get("steps", []):
                task.steps.append(TaskStep(
                    step_id=step_data["step_id"],
                    description=step_data["description"],
                    assigned_agent=step_data["assigned_agent"],
                ))
            return task
        except (KeyError, TypeError) as e:
            logger.error(f"Invalid task JSON format: {e}")
            return None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def elapsed(self) -> timedelta:
        if self._start_time:
            return datetime.now() - self._start_time
        return timedelta()

    @property
    def progress(self) -> dict:
        """获取当前进度信息"""
        p = {
            "running": self._running,
            "loop": self._loop_count,
            "completed": len(self._completed_tasks),
            "failed": len(self._failed_tasks),
            "in_progress": len(self._in_progress),
            "elapsed": str(self.elapsed),
            "remaining": str(self.max_duration - self.elapsed),
        }
        if self.inverse_verifier:
            ts = self.inverse_verifier.trust_statistics
            p["trust"] = {
                "trusted": ts["trusted"],
                "consecutive_pass": ts["consecutive_pass"],
                "required": ts["required"],
                "progress_pct": ts["progress_pct"],
                "pass_rate": ts.get("pass_rate", 0),
            }
            p["verification_history"] = \
                self.inverse_verifier.get_verification_history(limit=5)
        return p