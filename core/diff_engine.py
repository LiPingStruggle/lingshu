"""
DiffEngine - 差异引擎

需求覆盖（第 7 章/第 14 章）：自动对比 diff、生成代码差异报告
"""
from __future__ import annotations
import difflib
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class DiffEntry:
    """单个差异条目"""
    def __init__(self, file_path: str, old_content: str, new_content: str):
        self.file_path = file_path
        self.old_content = old_content
        self.new_content = new_content

    @property
    def unified_diff(self) -> str:
        return "\n".join(difflib.unified_diff(
            self.old_content.splitlines(),
            self.new_content.splitlines(),
            fromfile=f"a/{self.file_path}",
            tofile=f"b/{self.file_path}",
        ))

    @property
    def has_changes(self) -> bool:
        return self.old_content != self.new_content

    @property
    def lines_added(self) -> int:
        old_lines = self.old_content.splitlines()
        new_lines = self.new_content.splitlines()
        matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
        added = 0
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "insert":
                added += j2 - j1
            elif tag == "replace":
                added += j2 - j1
        return added

    @property
    def lines_removed(self) -> int:
        old_lines = self.old_content.splitlines()
        new_lines = self.new_content.splitlines()
        matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
        removed = 0
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "delete":
                removed += i2 - i1
            elif tag == "replace":
                removed += i2 - i1
        return removed


class DiffEngine:
    """差异引擎"""

    def __init__(self):
        self._entries: list[DiffEntry] = []

    def add(self, file_path: str, old_content: str, new_content: str) -> DiffEntry:
        entry = DiffEntry(file_path, old_content, new_content)
        self._entries.append(entry)
        return entry

    def generate_patch(self) -> str:
        """生成完整补丁"""
        parts = []
        for entry in self._entries:
            if entry.has_changes:
                parts.append(entry.unified_diff)
        return "\n".join(parts)

    def summary(self) -> dict:
        total_added = 0
        total_removed = 0
        changed_files = 0
        for entry in self._entries:
            if entry.has_changes:
                changed_files += 1
                total_added += entry.lines_added
                total_removed += entry.lines_removed
        return {
            "changed_files": changed_files,
            "lines_added": total_added,
            "lines_removed": total_removed,
            "total_entries": len(self._entries),
        }

    def clear(self) -> None:
        self._entries.clear()