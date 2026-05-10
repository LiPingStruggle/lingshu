#!/usr/bin/env python3
# 灵枢 (LingShu) - 智能调度总控，道法自然，任务不辍
"""
commands/run.py — CLI run 子命令（含 --mode 支持）

lingshu run "修复 app.py 空指针" --mode task --depth deep
"""
from __future__ import annotations
import asyncio
import logging
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from core.orchestrator import Orchestrator
from core.orchestration_core import IntelligentOrchestrator

console = Console()
logger = logging.getLogger("lingshu")


async def _do_run(
    description: str,
    mode: str = "task",
    depth: str = "medium",
    code_file: str = "",
    db: str = "lingshu.db",
) -> dict:
    """实际执行 run 逻辑"""
    orchestrator = Orchestrator()

    result = await orchestrator.start_run(
        user_input=description,
        mode=mode,
        yes=(mode in ("task", "auto-approve", "cascade")),
    )
    return result


def run_command(
    description: str,
    mode: str = typer.Option("task", "--mode", "-m", help="task|analysis|auto-approve|manual|cascade|debate"),
    depth: str = typer.Option("medium", "--depth", "-d", help="light|medium|deep"),
    code_file: str = typer.Option("", "--code", "-c", help="源码文件路径"),
    log_file: str = typer.Option("", "--log", "-l", help="日志文件路径"),
    db: str = typer.Option("lingshu.db", "--db"),
):
    """提交并执行单个任务（支持 6 种执行模式）"""
    console.print(f"[bold cyan]LingShu Run[/bold cyan] — mode={mode}, depth={depth}")
    console.print(f"  Task: {description[:100]}")

    if mode == "analysis":
        console.print("[yellow]Analysis mode: will stop after root cause[/yellow]")
    elif mode == "manual":
        console.print("[yellow]Manual mode: each step requires confirmation[/yellow]")
    elif mode == "debate":
        console.print("[yellow]Debate mode: proposer vs challenger + judge[/yellow]")

    result = asyncio.run(_do_run(description, mode=mode, depth=depth, code_file=code_file, db=db))

    status = result.get("status", "unknown")
    status_style = "green" if status == "completed" else "red"
    console.print(f"\n[bold]Result: [/{status_style}]{status}[/bold]")

    phases = result.get("phases", [])
    if phases:
        table = Table(title="Phase Results")
        table.add_column("Phase", style="cyan")
        table.add_column("Status", style="green")
        table.add_column("Detail")
        for p in phases:
            detail = (p.get("summary", "") or "")
            table.add_row(p.get("name", "?"), p.get("status", "?"), detail)
        console.print(table)

    console.print()
    for p in phases:
        name = p.get("name", "?")
        summary = p.get("summary", "") or ""
        feedback = p.get("feedback", "") or ""
        score = p.get("quality_score", "")
        
        if summary or feedback:
            panel = Panel(
                f"[bold]分阶段详情:[/bold] {name}\n\n"
                f"{summary}\n"
                + (f"\n[bold]反馈:[/bold] {feedback}" if feedback else "")
                + (f"\n[bold]质量评分:[/bold] {score}" if score != "" else ""),
                title=f"📋 {name} 详情",
                border_style="cyan",
            )
            console.print(panel)

    global_result = result.get("global_result") or phases[0].get("global_result", "") if phases else ""
    if global_result:
        console.print("\n[bold]Final Report:[/bold]")
        console.print(global_result[:500])

    return result