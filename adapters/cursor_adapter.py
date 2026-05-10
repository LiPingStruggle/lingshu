#!/usr/bin/env python3
"""
CursorAdapter - Cursor / OpenCode 接口
"""
from __future__ import annotations
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class CursorAdapter:
    """Cursor 风格上下文引用适配器"""

    def __init__(self):
        self._index: Dict[str, str] = {}

    def index_file(self, file_path: str, content: str) -> None:
        self._index[file_path] = content

    def resolve_reference(self, ref: str) -> Optional[str]:
        """解析 @ 引用为文件内容"""
        for path, content in self._index.items():
            if ref in path:
                return f"```{path}\n{content[:500]}...\n```"
        return None

    def get_context_for_prompt(self, files: List[str]) -> str:
        """构建上下文引用"""
        parts = []
        for f in files:
            resolved = self.resolve_reference(f)
            if resolved:
                parts.append(resolved)
        return "\n\n".join(parts)