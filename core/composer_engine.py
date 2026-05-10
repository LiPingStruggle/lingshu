#!/usr/bin/env python3
"""
ComposerEngine - Composer 多文件编辑引擎

支持在多个文件中同时进行编辑操作。
来源：Cursor Composer 多文件编辑理念。
"""
from __future__ import annotations
import asyncio
import difflib
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ComposerEdit:
    """单个编辑操作"""
    file_path: str
    old_string: str
    new_string: str
    applied: bool = False
    error: Optional[str] = None
    backup_path: Optional[str] = None


@dataclass
class ComposerResult:
    """Composer 执行结果"""
    edits: list[ComposerEdit] = field(default_factory=list)
    success_count: int = 0
    fail_count: int = 0
    patch: str = ""


class ComposerEngine:
    """
    Composer 多文件编辑引擎

    在多个文件中应用精确的 old_string → new_string 替换。
    支持:
    - 多文件并行编辑
    - 自动备份（.bak）
    - diff 生成
    - 回滚
    """

    def __init__(self, create_backup: bool = True, backup_dir: str = ".lingshu/backups"):
        self.create_backup = create_backup
        self.backup_dir = backup_dir
        self._history: list[ComposerResult] = []

    async def apply_edits(self, edits: list[ComposerEdit]) -> ComposerResult:
        """批量应用编辑"""
        result = ComposerResult()

        for edit in edits:
            try:
                if not os.path.isfile(edit.file_path):
                    edit.error = f"File not found: {edit.file_path}"
                    result.fail_count += 1
                    result.edits.append(edit)
                    continue

                with open(edit.file_path, "r", encoding="utf-8") as f:
                    content = f.read()

                if edit.old_string not in content:
                    edit.error = f"old_string not found in {edit.file_path}"
                    result.fail_count += 1
                    result.edits.append(edit)
                    continue

                # Create backup
                if self.create_backup and edit.old_string in content:
                    backup_path = os.path.join(
                        self.backup_dir,
                        f"{os.path.basename(edit.file_path)}.{os.path.basename(edit.file_path)}.bak"
                    )
                    os.makedirs(os.path.dirname(backup_path) or ".", exist_ok=True)
                    with open(backup_path, "w", encoding="utf-8") as f:
                        f.write(content)
                    edit.backup_path = backup_path

                # Apply edit
                new_content = content.replace(edit.old_string, edit.new_string, 1)
                with open(edit.file_path, "w", encoding="utf-8") as f:
                    f.write(new_content)

                edit.applied = True
                result.success_count += 1
                result.edits.append(edit)

            except Exception as e:
                edit.error = str(e)
                edit.applied = False
                result.fail_count += 1
                result.edits.append(edit)

        # Generate unified patch
        result.patch = self._generate_patch(result.edits)

        self._history.append(result)
        return result

    async def apply_from_string(self, text: str) -> ComposerResult:
        """从结构化文本解析并应用编辑

        格式:
        ```edit path/to/file.py
        --- old content
        +++ new content
        ```
        """
        import re
        edits = []

        # Match ```edit path ... ``` blocks
        pattern = r'```edit\s+(.+?)\n(.*?)```'
        for match in re.finditer(pattern, text, re.DOTALL):
            file_path = match.group(1).strip()
            block = match.group(2)

            # Try to parse unified diff format
            lines = block.split("\n")
            old_lines = []
            new_lines = []
            in_old = False
            in_new = False

            for line in lines:
                if line.startswith("--- "):
                    in_old = True
                    in_new = False
                    continue
                elif line.startswith("+++ "):
                    in_old = False
                    in_new = True
                    continue
                if in_old and line.startswith("- "):
                    old_lines.append(line[2:])
                elif in_new and line.startswith("+ "):
                    new_lines.append(line[2:])

            old_string = "\n".join(old_lines)
            new_string = "\n".join(new_lines)

            if old_string and new_string:
                edits.append(ComposerEdit(
                    file_path=file_path,
                    old_string=old_string,
                    new_string=new_string,
                ))
            elif old_string:
                # Delete mode
                edits.append(ComposerEdit(
                    file_path=file_path,
                    old_string=old_string,
                    new_string="",
                ))

        return await self.apply_edits(edits)

    def _generate_patch(self, edits: list[ComposerEdit]) -> str:
        """生成 unified diff 格式补丁"""
        parts = []
        for edit in edits:
            if edit.applied and os.path.isfile(edit.file_path):
                with open(edit.file_path, "r", encoding="utf-8") as f:
                    new_content = f.read()
                if edit.backup_path and os.path.isfile(edit.backup_path):
                    with open(edit.backup_path, "r", encoding="utf-8") as f:
                        old_content = f.read()
                    diff = difflib.unified_diff(
                        old_content.splitlines(),
                        new_content.splitlines(),
                        fromfile=f"a/{edit.file_path}",
                        tofile=f"b/{edit.file_path}",
                    )
                    parts.append("\n".join(diff))
        return "\n".join(parts)

    def rollback(self, index: int = -1) -> bool:
        """回滚指定（默认最近一次）Composer 操作"""
        if not self._history:
            return False
        result = self._history[index]
        for edit in result.edits:
            if edit.applied and edit.backup_path and os.path.isfile(edit.backup_path):
                try:
                    with open(edit.backup_path, "r", encoding="utf-8") as f:
                        old_content = f.read()
                    with open(edit.file_path, "w", encoding="utf-8") as f:
                        f.write(old_content)
                    logger.info(f"Rolled back {edit.file_path}")
                except Exception as e:
                    logger.error(f"Rollback failed for {edit.file_path}: {e}")
                    return False
        return True

    def get_history(self) -> list[dict]:
        """获取操作历史"""
        return [{
            "success_count": r.success_count,
            "fail_count": r.fail_count,
            "patch_length": len(r.patch),
        } for r in self._history]