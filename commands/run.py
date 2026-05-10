#!/usr/bin/env python3
# 灵枢 (LingShu) - 智能调度总控，道法自然，任务不辍
"""
commands/run.py — CLI run 子命令（含 --mode 支持）

lingshu run "修复 app.py 空指针" --mode task --depth deep
"""
from __future__ import annotations
import asyncio
import logging
import os
import glob
import re
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

from core.orchestrator import Orchestrator

logger = logging.getLogger('lingshu')
_console: Optional[Console] = None

