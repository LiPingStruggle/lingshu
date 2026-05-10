#!/usr/bin/env python3
"""
LingShu CLI - 主入口

使用方式:
  python main.py run <description>          # 提交单个任务
  python main.py batch                      # 批量执行任务清单
  python main.py resume <task_id>           # 从断点恢复
  python main.py list [status]              # 列出任务
  python main.py status <task_id>           # 查看任务状态
  python main.py server                     # 启动 RPC 服务
  python main.py engine <hours>             # 启动持续运行引擎
  python main.py smart <description>        # 智能调度模式（多模型+多Agent并发）
  python main.py map                          # 生成仓库地图
  python main.py template list                # 查看工作流模板
  python main.py template run <id> <vars>     # 运行工作流模板
  python main.py cost summary                 # 查看成本汇总
  python main.py plugin list                  # 列出已加载的插件
  python main.py plugin run <phase>           # 运行指定阶段的所有插件
"""
from __future__ import annotations
import asyncio
import json
import logging
import sys
import os
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.logging import RichHandler

# 设置 Windows 控制台编码为 UTF-8
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

app = typer.Typer(help="LingShu AI 自动化编程工具")
console = Console()

# 注册 agent
from core.agent_chain import AgentChain, Agent
from core.pipeline import Pipeline
from core.model_router import ModelRouter
from core.error_recovery import ErrorRecovery
from core.resource_monitor import ResourceMonitor
from core.inverse_verifier import InverseVerifier, InverseVerifierConfig
from core.engine import Engine
from core.feedback_learner import FeedbackLearner, format_teach_result
from core.profile_loader import ProfileLoader, ensure_default_profile
from core.orchestrator import Orchestrator
from core.orchestration_core import IntelligentOrchestrator, OrchestratorConfig


def _init_components(db_path: str = "lingshu.db", tasks_dir: str = "tasks"):
    """初始化所有子系统"""
    model_router = ModelRouter()
    model_router.register_default_models()

    resource_monitor = ResourceMonitor(max_concurrent=5)
    error_recovery = ErrorRecovery(model_router=model_router)
    agent_chain = AgentChain()
    pipeline = Pipeline(agent_chain=agent_chain, db_path=db_path)

    # 注册默认 Agent
    agent_chain.register_agents(
        light=Agent(
            name="light-executor",
            model_type="light",
            role="editor/executor",
            system_prompt="You are a fast, precise executor. Complete code tasks efficiently.",
        ),
        elite=Agent(
            name="elite-analyzer",
            model_type="elite",
            role="analyst/architect",
            system_prompt="You are a senior engineer. Analyze problems deeply and plan solutions.",
        ),
        strong=Agent(
            name="strong-reviewer",
            model_type="strong",
            role="reviewer/approver",
            system_prompt="You are a tech lead. Review work for quality, correctness, and completeness.",
        ),
    )

    # 初始化反向验证器
    verifier_config = InverseVerifierConfig(
        enabled=True,
        default_intensity="medium",
        consecutive_pass_required=10,
        trust_db_path=os.path.join(
            os.path.dirname(os.path.abspath(db_path)) or ".",
            ".lingshu", "trust.db"
        ),
    )
    inverse_verifier = InverseVerifier(agent_chain=agent_chain, config=verifier_config)
    pipeline.inverse_verifier = inverse_verifier

    engine = Engine(
        pipeline=pipeline,
        agent_chain=agent_chain,
        error_recovery=error_recovery,
        resource_monitor=resource_monitor,
        inverse_verifier=inverse_verifier,
        tasks_dir=tasks_dir,
    )

    return {
        "model_router": model_router,
        "resource_monitor": resource_monitor,
        "error_recovery": error_recovery,
        "agent_chain": agent_chain,
        "pipeline": pipeline,
        "engine": engine,
        "inverse_verifier": inverse_verifier,
    }


@app.command()
def run(
    description: str,
    task_id: Optional[str] = typer.Option(None, "--id", "-i"),
    mode: str = typer.Option("task", "--mode", "-m", help="task|analysis|auto-approve|manual|cascade|debate"),
    depth: str = typer.Option("medium", "--depth", "-d", help="light|medium|deep"),
    code_file: str = typer.Option("", "--code", "-c", help="源码文件路径"),
    log_file: str = typer.Option("", "--log", "-l", help="日志文件路径"),
    db: str = typer.Option("lingshu.db", "--db", help="Database path"),
):
    """提交并执行单个任务（支持 6 种执行模式）"""
    console.print(f"[bold cyan]LingShu Run[/bold cyan] — {description[:80]}")

    components = _init_components(db_path=db)
    model_router = components["model_router"]
    error_recovery = components["error_recovery"]
    agent_chain = components["agent_chain"]
    pipeline = components["pipeline"]

    orchestrator = Orchestrator(
        agent_chain=agent_chain,
        pipeline=pipeline,
        model_router=model_router,
        error_recovery=error_recovery,
    )
    result = asyncio.run(orchestrator.start_run(
        user_input=description,
        mode=mode,
        yes=(mode in ("task", "auto-approve", "cascade")),
    ))

    status = result.get("status", "unknown")
    status_style = "green" if status in ("completed",) else "red"
    console.print(f"\n[bold]Result: [{status_style}]{status}[/{status_style}][/bold]")

    phases = result.get("phases", [])
    if phases:
        table = Table(title="Phase Results")
        table.add_column("Phase", style="cyan")
        table.add_column("Status", style="green")
        table.add_column("Detail")
        for p in phases:
            table.add_row(
                p.get("name", "?"),
                p.get("status", "?"),
                (p.get("summary", "") or ""),
            )
        console.print(table)

    global_result = result.get("global_result") or (phases[0].get("global_result", "") if phases else "")
    if global_result:
        console.print(f"\nResult: {global_result[:500]}")


@app.command()
def batch(
    tasks_dir: str = typer.Option("tasks", "--dir", "-d"),
    db: str = typer.Option("lingshu.db", "--db"),
    max_hours: int = typer.Option(10, "--max-hours", "-t"),
    max_concurrent: int = typer.Option(3, "--concurrent", "-c"),
):
    """批量执行所有任务清单中的任务"""
    components = _init_components(db_path=db, tasks_dir=tasks_dir)
    engine = components["engine"]
    # 覆盖 engine 的时间配置
    engine.max_duration = __import__("datetime").timedelta(hours=max_hours)

    console.print(f"[bold cyan]Starting batch run[/bold cyan]")
    console.print(f"  Tasks dir: {tasks_dir}")
    console.print(f"  Max hours: {max_hours}")
    console.print(f"  Max concurrent: {max_concurrent}")

    stats = asyncio.run(engine.start())

    result_table = Table(title="Batch Run Results")
    result_table.add_column("Metric", style="cyan")
    result_table.add_column("Value", style="green")
    result_table.add_row("Completed", str(stats["completed"]))
    result_table.add_row("Failed", str(stats["failed"]))
    result_table.add_row("Retries", str(stats["retries"]))
    result_table.add_row("Loop Count", str(stats["loop_count"]))
    result_table.add_row("Elapsed", stats.get("elapsed", "N/A"))
    result_table.add_row("Status", stats["status"])
    console.print(result_table)

    if stats["errors"]:
        console.print("[bold red]Errors:[/bold red]")
        for err in stats["errors"]:
            console.print(f"  - {err}")


@app.command()
def resume(
    task_id: str,
    db: str = typer.Option("lingshu.db", "--db"),
):
    """从检查点恢复失败任务"""
    components = _init_components(db_path=db)
    pipeline = components["pipeline"]

    result = asyncio.run(pipeline.resume(task_id))
    if result:
        console.print(f"[green]Task {task_id} resumed, status: {result.status}[/green]")
        if result.result:
            console.print(f"Result: {result.result[:200]}")
    else:
        console.print(f"[red]Task {task_id} not found[/red]")


@app.command()
def list(
    status: Optional[str] = typer.Argument(None, help="Filter by status"),
    db: str = typer.Option("lingshu.db", "--db"),
):
    """列出所有任务"""
    components = _init_components(db_path=db)
    pipeline = components["pipeline"]

    tasks = pipeline.list_tasks(status=status)
    table = Table(title=f"Tasks ({status or 'all'})")
    table.add_column("Task ID", style="cyan")
    table.add_column("Description", style="white")
    table.add_column("Type", style="blue")
    table.add_column("Status", style="green")
    table.add_column("Created", style="dim")

    for t in tasks:
        table.add_row(
            t["task_id"],
            t["description"][:50],
            t["type"],
            t["status"],
            t["created_at"][:19],
        )
    console.print(table)


@app.command()
def status(
    task_id: str,
    db: str = typer.Option("lingshu.db", "--db"),
):
    """查看单个任务详情"""
    components = _init_components(db_path=db)
    pipeline = components["pipeline"]

    task = pipeline.get_task(task_id)
    if not task:
        console.print(f"[red]Task {task_id} not found[/red]")
        raise typer.Exit(1)

    table = Table(title=f"Task {task_id}")
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Description", task.description)
    table.add_row("Type", task.type)
    table.add_row("Status", task.status)
    table.add_row("Dependencies", ", ".join(task.dependencies) or "None")
    table.add_row("Steps", str(len(task.steps)))
    table.add_row("Created", task.created_at or "N/A")
    table.add_row("Updated", task.updated_at or "N/A")
    if task.result:
        table.add_row("Result", task.result[:200])
    if task.error:
        table.add_row("Error", task.error, style="red")
    console.print(table)

    # 显示步骤
    if task.steps:
        step_table = Table(title="Steps")
        step_table.add_column("Step", style="cyan")
        step_table.add_column("Description")
        step_table.add_column("Agent", style="blue")
        step_table.add_column("Status", style="green")
        for s in task.steps:
            step_table.add_row(
                s.step_id,
                s.description[:40],
                s.assigned_agent,
                s.status,
            )
        console.print(step_table)


@app.command()
def server(
    host: str = typer.Option("127.0.0.1", "--host", "-h"),
    port: int = typer.Option(8000, "--port", "-p"),
    db: str = typer.Option("lingshu.db", "--db"),
):
    """启动 FastAPI RPC 服务"""
    console.print(f"[bold cyan]Starting RPC server on {host}:{port}...[/bold cyan]")
    # 导入并运行 rpc_server
    from rpc_server import start_server
    asyncio.run(start_server(host=host, port=port, db_path=db))


@app.command()
def engine(
    hours: int = typer.Argument(10, help="Run duration in hours"),
    tasks_dir: str = typer.Option("tasks", "--dir", "-d"),
    db: str = typer.Option("lingshu.db", "--db"),
):
    """启动持续运行引擎（核心功能）"""
    console.print("[bold green]" + "=" * 50 + "[/bold green]")
    console.print("[bold green]      LINGSHU ENGINE - CONTINUOUS RUN MODE[/bold green]")
    console.print(f"[bold green]      Duration: {hours}h | Tasks: {tasks_dir}[/bold green]")
    console.print("[bold green]" + "=" * 50 + "[/bold green]")

    components = _init_components(db_path=db, tasks_dir=tasks_dir)
    engine_instance = components["engine"]
    engine_instance.max_duration = __import__("datetime").timedelta(hours=hours)

    stats = asyncio.run(engine_instance.start())

    console.print("[bold cyan]Engine finished[/bold cyan]")
    result_table = Table(title="Engine Results")
    result_table.add_column("Metric", style="cyan")
    result_table.add_column("Value", style="green")
    result_table.add_row("Completed", str(stats["completed"]))
    result_table.add_row("Failed", str(stats["failed"]))
    result_table.add_row("Retries", str(stats["retries"]))
    result_table.add_row("Loop Count", str(stats["loop_count"]))
    result_table.add_row("Elapsed", stats.get("elapsed", "N/A"))
    result_table.add_row("Status", stats["status"])
    console.print(result_table)

    # 显示信任状态
    iv = components.get("inverse_verifier")
    if iv:
        ts = iv.trust_statistics
        trust_table = Table(title="Verifier Trust Status")
        trust_table.add_column("Metric", style="cyan")
        trust_table.add_column("Value", style="green")
        trust_table.add_row("Trusted", str(ts["trusted"]))
        trust_table.add_row("Consecutive Pass", f"{ts['consecutive_pass']}/{ts['required']}")
        trust_table.add_row("Progress", f"{ts['progress_pct']}%")
        trust_table.add_row("Pass Rate", f"{ts['pass_rate']}%")
        trust_table.add_row("Total Verified", str(ts["total_verified"]))
        console.print(trust_table)


@app.command()
def trust(
    action: str = typer.Argument("status", help="status|reset|history"),
    db: str = typer.Option("lingshu.db", "--db"),
):
    """查看或管理反向验证器信任状态"""
    components = _init_components(db_path=db)
    iv = components["inverse_verifier"]

    if action == "reset":
        iv.reset_trust()
        console.print("[red]Trust state reset to zero[/red]")
        return

    ts = iv.trust_statistics
    table = Table(title="Verifier Trust Status")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Environment Trusted", str(ts["trusted"]))
    table.add_row("Consecutive Pass", f"{ts['consecutive_pass']}/{ts['required']}")
    table.add_row("Trust Progress", f"{ts['progress_pct']}%")
    table.add_row("Total Verified", str(ts["total_verified"]))
    table.add_row("Total Pass", str(ts["total_pass"]))
    table.add_row("Total Fail", str(ts["total_fail"]))
    table.add_row("Pass Rate", f"{ts['pass_rate']}%")
    table.add_row("Trust DB", ts["trust_db"])
    console.print(table)

    if action == "history":
        history = iv.get_verification_history(limit=20)
        if history:
            hist_table = Table(title="Verification History (recent)")
            hist_table.add_column("Task ID", style="cyan")
            hist_table.add_column("Passed", style="green")
            hist_table.add_column("Confidence", style="blue")
            hist_table.add_column("Consecutive", style="yellow")
            hist_table.add_column("Timestamp", style="dim")
            for h in history:
                hist_table.add_row(
                    h["task_id"],
                    "Y" if h["passed"] else "N",
                    f"{h['confidence']:.1f}%",
                    str(h["consecutive_at_time"]),
                    h["timestamp"][:19],
                )
            console.print(hist_table)


@app.command()
def teach(
    text: str = typer.Argument(..., help="要教会 AI 的规则，例如 'teach coding_style = 函数式优先'"),
    scope: str = typer.Option("project", "--scope", "-s", help="project|global"),
):
    """教会 AI 一条规则（直接写入画像）"""
    ensure_default_profile()
    learner = FeedbackLearner()
    result = learner.teach_from_text(text, scope=scope)
    console.print(format_teach_result(result))
    note = result.get("note")
    if note:
        console.print(f"[dim]提示: {note}[/dim]")


@app.command()
def feedback(
    action: str = typer.Argument("stats", help="stats|suggestions|evolve|history"),
    db: str = typer.Option("lingshu.db", "--db"),
):
    """查看反馈学习器的状态和建议"""
    ensure_default_profile()
    learner = FeedbackLearner()

    if action == "stats":
        stats = learner.get_stats()
        table = Table(title="Feedback Learner Stats")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")
        table.add_row("Total Actions", str(stats["total_actions"]))
        table.add_row("Patterns", str(len(stats["patterns"])))
        table.add_row("Profile Keys", str(len(stats["profile"])))
        if stats["command_counts"]:
            top_cmds = ", ".join(stats["command_counts"].keys())
            table.add_row("Top Commands", top_cmds)
        if stats["flag_counts"]:
            top_flags = ", ".join(stats["flag_counts"].keys())
            table.add_row("Top Flags", top_flags)
        if stats["reject_counts"]:
            top_rejects = ", ".join(stats["reject_counts"].keys())
            table.add_row("Top Rejects", top_rejects)
        console.print(table)

    elif action == "suggestions":
        suggestions = learner.get_evolve_suggestions()
        if not suggestions:
            console.print("[yellow]尚无进化建议。使用更多命令后会自动生成。[/yellow]")
            return
        table = Table(title="Evolve Suggestions")
        table.add_column("#", style="dim")
        table.add_column("Message", style="white")
        for i, s in enumerate(suggestions, 1):
            table.add_row(str(i), s["message"])
        console.print(table)

    elif action == "evolve":
        result = learner.evolve_profile()
        if result["evolved"]:
            console.print(f"[green]进化了 {result['evolved']} 条规则[/green]")
            for r in result["rules"]:
                console.print(f"  + {r}")
            console.print(f"[dim]总模式数: {result['total_patterns']}[/dim]")
        else:
            console.print("[yellow]暂无新规则可进化。使用 `main.py feedback stats` 查看当前数据。[/yellow]")

    elif action == "history":
        actions = learner.get_recent_actions(30)
        if not actions:
            console.print("[yellow]暂无行为记录。[/yellow]")
            return
        table = Table(title="Recent User Actions")
        table.add_column("Type", style="cyan")
        table.add_column("Key", style="white")
        table.add_column("Success", style="green")
        table.add_column("Timestamp", style="dim")
        for a in actions[-20:]:
            table.add_row(
                a["action_type"],
                a["key"][:30],
                "Y" if a["success"] else "N",
                a["timestamp"][:19],
            )
        console.print(table)


@app.command()
def smart(
    description: str = typer.Argument(..., help="任务描述"),
    db: str = typer.Option("lingshu.db", "--db"),
):
    """智能调度模式：多模型多Agent并发执行，自动降级+审查"""
    console.print("[bold cyan]" + "=" * 50 + "[/bold cyan]")
    console.print("[bold cyan]      LINGSHU INTELLIGENT ORCHESTRATOR[/bold cyan]")
    console.print(f"[bold cyan]      Mode: smart[/bold cyan]")
    console.print("[bold cyan]" + "=" * 50 + "[/bold cyan]")

    # 初始化智能调度总控
    intelligent_orch = IntelligentOrchestrator()
    orchestrator = Orchestrator(
        intelligent_orchestrator=intelligent_orch,
    )

    result = asyncio.run(orchestrator.start_run(
        user_input=description,
        mode="smart",
        yes=True,
    ))

    # 显示结果
    console.print("\n[bold]执行报告:[/bold]")
    table = Table(title="智能调度结果")
    table.add_column("指标", style="cyan")
    table.add_column("值", style="green")
    table.add_row("状态", result["status"])

    phases = result.get("phases", [])
    if phases:
        p = phases[0]
        table.add_row("复杂度", str(p.get("complexity", "N/A")))
        am = p.get("assigned_models", {})
        table.add_row("Planner", am.get("planner", "N/A"))
        workers = am.get("workers", [])
        table.add_row("Workers", str(len(workers)))
        table.add_row("子任务数", str(len(p.get("sub_tasks", []))))
        table.add_row("并行失败", str(p.get("parallel_failures", 0)))
        table.add_row("审查通过", str(p.get("review_passed", "N/A")))
        table.add_row("审查评分", str(p.get("review_score", "N/A")))
    console.print(table)

    if phases and phases[0].get("global_result"):
        console.print("\n[bold]最终报告:[/bold]")
        console.print(phases[0]["global_result"][:1000])


@app.command()
def map(
    max_tokens: int = typer.Option(2000, "--max-tokens", "-t", help="Max tokens for map"),
):
    """生成仓库地图（CodeMap）"""
    from core.code_map import CodeMap

    cm = CodeMap(os.getcwd(), max_map_tokens=max_tokens)
    cm.build()
    repo_map = cm.generate_map()

    console.print("[bold cyan]Repository Map[/bold cyan]")
    console.print(repo_map)
    console.print(f"\n[dim]Stats: {cm.stats['files']} files, {cm.stats['lines']} lines, {cm.stats['symbols']} symbols[/dim]")


@app.command()
def cost(
    action: str = typer.Argument("summary", help="summary|routes"),
    db: str = typer.Option("lingshu.db", "--db"),
):
    """查看成本追踪信息"""
    from core.cost_tracker import CostTracker
    from core.model_router import ModelRouter
    from core.cost_aware_router import CostAwareRouter

    tracker = CostTracker()
    router = ModelRouter()
    router.register_default_models()
    cost_router = CostAwareRouter(router, tracker)

    if action == "summary":
        s = tracker.stats
        table = Table(title="Cost Summary")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")
        table.add_row("Total Requests", str(s["total_requests"]))
        table.add_row("Total Cost (USD)", f"${s['total_cost_usd']}")
        table.add_row("Total Input Tokens", f"{s['total_tokens']['input']:,}")
        table.add_row("Total Output Tokens", f"{s['total_tokens']['output']:,}")
        console.print(table)

        if s["by_provider"]:
            prov_table = Table(title="Per Provider")
            prov_table.add_column("Provider", style="cyan")
            prov_table.add_column("Requests")
            prov_table.add_column("Cost (USD)")
            prov_table.add_column("Input Tokens")
            prov_table.add_column("Output Tokens")
            for prov, data in s["by_provider"].items():
                prov_table.add_row(
                    prov, str(data["requests"]),
                    f"${data['cost']}",
                    f"{data['input_tokens']:,}",
                    f"{data['output_tokens']:,}",
                )
            console.print(prov_table)

    elif action == "routes":
        cr_summary = cost_router.summary()
        table = Table(title="Cost-Aware Routing Summary")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")
        table.add_row("Total Routes", str(cr_summary["total_routes"]))
        table.add_row("Daily Budget", f"${cr_summary['daily_budget_usd']}")
        table.add_row("Spent Today", f"${cr_summary['spent_today_usd']}")
        table.add_row("Budget Tier", cr_summary["budget_tier"])
        console.print(table)

        if cr_summary["model_usage"]:
            mu_table = Table(title="Model Usage")
            mu_table.add_column("Model", style="cyan")
            mu_table.add_column("Times Selected", style="green")
            for model, count in cr_summary["model_usage"].items():
                mu_table.add_row(model, str(count))
            console.print(mu_table)


@app.command()
def plugin(
    action: str = typer.Argument("list", help="list|run"),
    phase: str = typer.Option("", "--phase", "-p", help="Phase name to execute"),
):
    """管理并运行插件系统"""
    from plugins.manager import PluginManager
    import yaml

    config_path = "lingshu.yaml"
    if not os.path.exists(config_path):
        console.print(f"[red]Config not found: {config_path}[/red]")
        raise typer.Exit(1)

    with open(config_path) as f:
        config = yaml.safe_load(f) or {}

    mgr = PluginManager(config)

    if action == "list":
        asyncio.run(mgr.load_plugins())
        plugins_list = mgr.list_plugins()
        stats = mgr.stats

        table = Table(title="Loaded Plugins")
        table.add_column("Name", style="cyan")
        table.add_column("Description")
        table.add_column("Phase")
        table.add_column("Enabled", style="green")
        for p in plugins_list:
            table.add_row(p["name"], p["description"], p["phase"], str(p["enabled"]))
        console.print(table)
        console.print(f"[dim]{stats['total']} plugin(s) across {len(stats['phases'])} phase(s)[/dim]")

    elif action == "run":
        if not phase:
            console.print("[red]--phase is required when action is 'run'[/red]")
            raise typer.Exit(1)
        asyncio.run(mgr.load_plugins())
        state = {"phase": phase}
        result = asyncio.run(mgr.execute_phase(phase, state))
        console.print(f"[green]Phase '{phase}' executed[/green]")
        skipped = result.get("plugin_skipped", [])
        errors = result.get("plugin_errors", {})
        if skipped:
            console.print(f"[yellow]Skipped: {', '.join(skipped)}[/yellow]")
        if errors:
            console.print(f"[red]Errors: {list(errors.keys())}[/red]")


@app.command()
def configure():
    """打开 lingshu.yaml 配置文件进行编辑"""
    from commands.configure import configure_command
    configure_command()


@app.command()
def history(
    limit: int = typer.Option(20, "--limit", "-n", help="显示条数"),
    status: Optional[str] = typer.Option(None, "--status", "-s", help="按状态过滤"),
    db: str = typer.Option("lingshu.db", "--db"),
):
    """查看任务历史"""
    from commands.history import history_command
    history_command(limit=limit, status=status, db=db)


@app.command()
def stats(
    db: str = typer.Option("lingshu.db", "--db"),
):
    """查看系统统计信息（任务数、成功率、运行时长）"""
    from core.agent_chain import AgentChain
    from core.pipeline import Pipeline

    agent_chain = AgentChain()
    pipeline = Pipeline(agent_chain=agent_chain, db_path=db)

    tasks = pipeline.list_tasks()
    total = len(tasks)
    done = sum(1 for t in tasks if t["status"] in ("done", "completed"))
    failed = sum(1 for t in tasks if t["status"] == "failed")
    running = sum(1 for t in tasks if t["status"] == "running")
    pending = sum(1 for t in tasks if t["status"] == "pending")
    success_rate = round(done / total * 100, 1) if total > 0 else 0

    table = Table(title="System Stats")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")
    table.add_row("Total Tasks", str(total))
    table.add_row("Completed", str(done))
    table.add_row("Failed", str(failed))
    table.add_row("Running", str(running))
    table.add_row("Pending", str(pending))
    table.add_row("Success Rate", f"{success_rate}%")

    # 成本统计数据
    try:
        from core.cost_tracker import CostTracker
        tracker = CostTracker()
        s = tracker.stats
        table.add_row("Total Cost (USD)", f"${s['total_cost_usd']}")
        table.add_row("Total API Calls", str(s["total_requests"]))
        table.add_row("Total Input Tokens", f"{s['total_tokens']['input']:,}")
        table.add_row("Total Output Tokens", f"{s['total_tokens']['output']:,}")
    except Exception:
        pass

    console.print(table)


if __name__ == "__main__":
    app()


def main():
    """lingshu CLI 入口（通过 pip 安装后可直接调用）"""
    app()