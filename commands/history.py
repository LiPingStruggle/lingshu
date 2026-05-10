#!/usr/bin/env python3
# 灵枢 (LingShu) - 智能调度总控，道法自然，任务不辍
"""
commands/history.py — 查看任务历史和执行统计

lingshu history
lingshu history --limit 50
lingshu history --status failed
"""
from __future__ import annotations
import asyncio
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table

from core.pipeline import Pipeline
from core.agent_chain import AgentChain

console = Console()


def history_command(
    limit: int = typer.Option(20, "--limit", "-n", help="显示条数"),
    status: Optional[str] = typer.Option(None, "--status", "-s", help="按状态过滤"),
    db: str = typer.Option("lingshu.db", "--db"),
):
    """查看任务历史"""
    agent_chain = AgentChain()
    pipeline = Pipeline(agent_chain=agent_chain, db_path=db)

    tasks = pipeline.list_tasks(status=status)

    if not tasks:
        console.print("[yellow]No tasks found[/yellow]")
        return

    # 倒序取最新
    tasks = tasks[-limit:]

    table = Table(title=f"Task History (last {len(tasks)})")
    table.add_column("ID", style="cyan", no_wrap=True)
    table.add_column("Description", style="white")
    table.add_column("Type", style="blue")
    table.add_column("Status", style="green")
    table.add_column("Steps", style="dim")
    table.add_column("Created", style="dim")

    for t in tasks:
        status_style = {
            "done": "green",
            "completed": "green",
            "failed": "red",
            "running": "yellow",
            "pending": "white",
        }.get(t["status"], "white")
        table.add_row(
            t["task_id"],
            t["description"][:50],
            t["type"],
            f"[{status_style}]{t['status']}[/{status_style}]",
            str(t.get("steps", "?")),
            t["created_at"][:19] if t.get("created_at") else "N/A",
        )
    console.print(table)

    # 统计
    done = sum(1 for t in tasks if t["status"] in ("done", "completed"))
    failed = sum(1 for t in tasks if t["status"] == "failed")
    console.print(f"\n[dim]Summary: {done} done, {failed} failed, {len(tasks)} total[/dim]")