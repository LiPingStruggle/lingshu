#!/usr/bin/env python3
"""
IntentParser - 交互解析器

职责:
  将用户模糊输入解析为结构化 ParedIntent 对象

支持:
  - @file.py / @path/to/file → 自动加载文件内容
  - @task:<id> → 从任务历史加载
  - @memory:<key> → 从长期记忆提取
  - --fast / --deep 策略标记
  - 模糊指令自动增强（附加目录结构）
"""
from __future__ import annotations
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class Strategy:
    FAST = "fast"
    DEEP = "deep"
    NORMAL = "normal"


@dataclass
class ParsedIntent:
    """解析后的用户意图"""
    raw_text: str
    resolved_text: str = ""
    referenced_files: list[dict] = field(default_factory=list)  # [{path, content}]
    referenced_tasks: list[dict] = field(default_factory=list)  # [{task_id, summary}]
    referenced_memories: list[str] = field(default_factory=list)
    strategy: str = Strategy.NORMAL
    has_attachments: bool = False

    def to_prompt_suffix(self) -> str:
        """将引用内容转为追加到 prompt 的文本"""
        parts = []
        if self.referenced_files:
            parts.append("\n--- 引用文件 ---")
            for f in self.referenced_files:
                parts.append(f"\n### {f['path']}")
                parts.append(f"```\n{f['content'][:3000]}\n```")
        if self.referenced_tasks:
            parts.append("\n--- 引用任务 ---")
            for t in self.referenced_tasks:
                parts.append(f"- {t['task_id']}: {t['summary'][:200]}")
        if self.referenced_memories:
            parts.append("\n--- 相关经验 ---")
            for m in self.referenced_memories:
                parts.append(f"  • {m[:300]}")
        return "\n".join(parts)


class IntentParser:
    """
    交互解析器 - 将自然语言输入转为结构化意图

    用法:
        parser = IntentParser()
        intent = parser.parse("修复 @src/auth.py 中的登录问题 --deep")
    """

    def __init__(self, tasks_db_path: str = "", memory=None):
        self.tasks_db_path = tasks_db_path
        self.memory = memory  # LongTermMemory 实例

        # 正则模式（编译一次提升性能）
        self._re_file = re.compile(r'@([\w\-./\\]+\.\w+)')
        self._re_task = re.compile(r'@task:([\w]+)')
        self._re_memory = re.compile(r'@memory:([\w\-]+)')
        self._re_fast = re.compile(r'\b--fast\b')
        self._re_deep = re.compile(r'\b--deep\b')

    def parse(self, text: str, cwd: str = "") -> ParsedIntent:
        """
        解析用户输入，返回 ParsedIntent

        参数:
            text: 用户原始输入
            cwd: 当前工作目录（用于 @ 文件路径解析）
        """
        raw = text.strip()
        cwd = cwd or os.getcwd()
        intent = ParsedIntent(raw_text=raw)

        # 1. 提取策略标记
        if self._re_fast.search(raw):
            intent.strategy = Strategy.FAST
            raw = self._re_fast.sub("", raw).strip()
        elif self._re_deep.search(raw):
            intent.strategy = Strategy.DEEP
            raw = self._re_deep.sub("", raw).strip()

        # 2. 解析 @file 引用
        raw = self._resolve_file_refs(raw, cwd, intent)

        # 3. 解析 @task 引用
        raw = self._resolve_task_refs(raw, intent)

        # 4. 解析 @memory 引用
        raw = self._resolve_memory_refs(raw, intent)

        # 5. 模糊指令增强
        raw = self._enhance_vague_input(raw, cwd, intent)

        intent.resolved_text = raw
        return intent

    def _resolve_file_refs(self, text: str, cwd: str, intent: ParsedIntent) -> str:
        """解析 @file 引用，加载文件内容"""
        def _load_file(match: re.Match) -> str:
            rel_path = match.group(1)
            # 尝试多种路径
            candidates = [
                Path(cwd) / rel_path,
                Path(rel_path),
                Path(cwd) / "src" / rel_path,
            ]
            for fp in candidates:
                fp = fp.resolve()
                if fp.exists() and fp.is_file():
                    try:
                        content = fp.read_text(encoding="utf-8")
                        intent.referenced_files.append({
                            "path": str(fp),
                            "content": content,
                            "rel_path": rel_path,
                        })
                        logger.info(f"Loaded referenced file: {fp}")
                        return f"[已加载: {fp.name}]"
                    except Exception as e:
                        logger.warning(f"Failed to read {fp}: {e}")
            logger.warning(f"Referenced file not found: {rel_path}")
            return f"[文件未找到: {rel_path}]"

        return self._re_file.sub(_load_file, text)

    def _resolve_task_refs(self, text: str, intent: ParsedIntent) -> str:
        """解析 @task 引用，从数据库加载任务"""
        def _load_task(match: re.Match) -> str:
            task_id = match.group(1)
            task_info = self._fetch_task(task_id)
            if task_info:
                intent.referenced_tasks.append(task_info)
                logger.info(f"Loaded referenced task: {task_id}")
                return f"[任务 {task_id}: {task_info['summary'][:60]}...]"
            return f"[任务 {task_id} 未找到]"

        return self._re_task.sub(_load_task, text)

    def _resolve_memory_refs(self, text: str, intent: ParsedIntent) -> str:
        """解析 @memory 引用，从长期记忆提取"""
        def _load_memory(match: re.Match) -> str:
            key = match.group(1)
            if self.memory:
                value = self.memory.retrieve(key)
                if value:
                    val_str = str(value)
                    intent.referenced_memories.append(val_str)
                    logger.info(f"Loaded referenced memory: {key}")
                    return f"[记忆 {key}: {val_str[:100]}...]"
            return f"[记忆 {key} 未找到]"

        return self._re_memory.sub(_load_memory, text)

    def _enhance_vague_input(self, text: str, cwd: str, intent: ParsedIntent) -> str:
        """
        模糊指令增强：如果输入很短且无文件引用，自动附加目录结构
        """
        # 如果已有关联文件或任务，说明上下文足够
        if intent.referenced_files or intent.referenced_tasks:
            return text

        # 短输入（<20 字）且无明确动词 → 可能模糊
        is_vague = len(text) < 20 and not any(
            kw in text for kw in ["修复", "实现", "添加", "删除", "重构", "优化",
                                  "fix", "add", "implement", "refactor", "update"]
        )
        if is_vague:
            try:
                work_dir = Path(cwd)
                items = list(work_dir.iterdir())[:30]
                structure_lines = ["\n\n--- 当前工作目录 ---"]
                for item in items:
                    if item.is_dir():
                        structure_lines.append(f"  📁 {item.name}/")
                    else:
                        size = item.stat().st_size
                        structure_lines.append(f"  📄 {item.name} ({size}B)")
                text += "\n".join(structure_lines)
            except Exception as e:
                logger.debug(f"Failed to list directory: {e}")

        return text

    def _fetch_task(self, task_id: str) -> Optional[dict]:
        """从 SQLite 加载任务"""
        if not self.tasks_db_path:
            return None
        try:
            import sqlite3
            conn = sqlite3.connect(self.tasks_db_path)
            c = conn.cursor()
            c.execute(
                "SELECT task_id, description, type, status FROM tasks WHERE task_id=?",
                (task_id,),
            )
            row = c.fetchone()
            conn.close()
            if row:
                return {"task_id": row[0], "summary": row[1], "type": row[2], "status": row[3]}
        except Exception as e:
            logger.warning(f"Failed to fetch task {task_id}: {e}")
        return None