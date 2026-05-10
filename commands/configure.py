#!/usr/bin/env python3
# 灵枢 (LingShu) - 智能调度总控，道法自然，任务不辍
"""
commands/configure.py — 打开配置文件

lingshu configure
"""
from __future__ import annotations
import os
import sys
import subprocess

import typer
from rich.console import Console

console = Console()


def configure_command():
    """打开 lingshu.yaml 配置文件进行编辑"""
    config_path = "lingshu.yaml"

    if not os.path.exists(config_path):
        console.print(f"[red]Config file not found: {config_path}[/red]")
        console.print("[yellow]Creating default lingshu.yaml...[/yellow]")
        _create_default_config(config_path)

    console.print(f"Opening config: [bold]{os.path.abspath(config_path)}[/bold]")

    # 尝试用编辑器打开
    editors = [
        os.environ.get("EDITOR"),
        os.environ.get("VISUAL"),
        "code",
        "vim",
        "nano",
        "notepad.exe" if sys.platform == "win32" else None,
    ]

    for editor in editors:
        if not editor:
            continue
        try:
            subprocess.run([editor, config_path], check=True)
            return
        except (FileNotFoundError, subprocess.CalledProcessError):
            continue

    console.print(f"[yellow]Please edit manually: {os.path.abspath(config_path)}[/yellow]")


def _create_default_config(path: str):
    """创建默认配置文件"""
    content = """\
# LingShu 配置文件
models:
  elite: ["claude-sonnet-4", "gpt-4.1"]
  strong: ["claude-sonnet-4", "gpt-4o", "deepseek-r1"]
  medium: ["gpt-4o", "deepseek-chat"]
  light: ["gpt-4o-mini", "deepseek-chat", "gemini-2.0-flash"]
  local: ["ollama/qwen2.5-coder:7b"]

fallback:
  max_retries: 3
  allow_downgrade: true

plugins:
  enabled: true
  phases:
    evidence_collection:
      - trailmark_callgraph
      - codebadger_inspect
    post_execution:
      - diting_verify
  trailmark:
    enabled: true
    auto_trigger: true
  codebadger:
    enabled: true
    auto_trigger: true
  diting:
    enabled: true
    depth: "medium"
    min_complexity: "moderate"

execution:
  max_rounds: 3
  max_recovery_attempts: 10
  checkpoint_enabled: true

logging:
  level: "DEBUG"
  file: "lingshu.log"
"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    console.print(f"[green]Created {path}[/green]")