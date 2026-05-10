#!/usr/bin/env python3
# 灵枢 (LingShu) - 智能调度总控，道法自然，任务不辍
"""
commands/resume.py — 从断点恢复中断的任务

lingshu resume <task_id>
"""
from __future__ import annotations
import asyncio

import typer
from rich.console import Console
from rich.table import Table

from core.agent_chain import AgentChain
from core.pipeline import Pipeline

console = Console()


def resume_command(
    task_id: str,
    db: str = typer.Option("lingshu.db", "--db"),
):
    """从检查点恢复中断的任务"""
    agent_chain = AgentChain()
    pipeline = Pipeline(agent_chain=agent_chain, db_path=db)

    task = pipeline.get_task(task_id)
    if not task:
        console.print(f"[red]Task {task_id} not found[/red]")
        raise typer.Exit(1)

    console.print(f"[bold cyan]Resuming task: {task_id}[/bold cyan]")
    console.print(f"  Description: {task.description}")
    console.print(f"  Current status: {task.status}")
    console.print(f"  Steps: {len(task.steps)} total")

    # 显示 checkpoint 状态
    completed_steps = sum(1 for s in task.steps if s.status == "done")
    failed_steps = sum(1 for s in task.steps if s.status == "failed")
    console.print(f"  Completed: {completed_steps}, Failed: {failed_steps}")

    if failed_steps == 0:
        console.print(f"[green]No failed steps found, task may already be complete.[/green]")
        return

    result = asyncio.run(pipeline.resume(task_id))

    if result:
        console.print(f"\n[green]Resume result: {result.status}[/green]")
        if result.result:
            console.print(f"Result: {result.result[:300]}")

        step_table = Table(title="Step Results")
        step_table.add_column("Step", style="cyan")
        step_table.add_column("Status", style="green")
        step_table.add_column("Result")
        for s in result.steps:
            step_table.add_row(
                s.step_id,
                "✓" if s.status == "done" else "✗",
                (s.result or s.error or "")[:80],
            )
        console.print(step_table)
    else:
        console.print(f"[red]Failed to resume task {task_id}[/red]")


# 快速恢复最后一个失败任务的便捷函数
def quick_resume(db: str = "lingshu.db"):
    """恢复最后一个状态为 failed 的任务"""
    agent_chain = AgentChain()
    pipeline = Pipeline(agent_chain=agent_chain, db_path=db)

    tasks = pipeline.list_tasks(status="failed")
    if not tasks:
        console.print("[yellow]No failed tasks to resume[/yellow]")
        return

    latest = tasks[-1]
    console.print(f"[cyan]Auto-resuming latest failed task: {latest['task_id']}[/cyan]")
    resume_command(latest["task_id"], db=db)