"""
WorkspaceManager - 工作区管理

需求覆盖（第 9 章/第 14 章）：
- Windows 本地盘 + 网络映射盘（Z:\\ Y:\\ 等）
- UNC 路径（\\\\server\\share）
- .lingshu/ 目录存储项目级配置
"""
from __future__ import annotations
import os
import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class WorkspaceManager:
    """工作区管理器"""

    def __init__(self, root_path: Optional[str] = None):
        self.root_path = os.path.abspath(root_path or os.getcwd())
        self.lingshu_dir = os.path.join(self.root_path, ".lingshu")
        os.makedirs(self.lingshu_dir, exist_ok=True)

    def resolve_path(self, path: str) -> str:
        """解析路径（支持 UNC 和映射盘）"""
        if path.startswith("\\\\") or path.startswith("//"):
            return os.path.abspath(path)
        if len(path) >= 2 and path[1] == ":":
            return os.path.abspath(path)
        return os.path.abspath(os.path.join(self.root_path, path))

    def is_in_workspace(self, path: str) -> bool:
        """检查路径是否在工作区内"""
        resolved = self.resolve_path(path)
        return resolved.startswith(self.root_path)

    def get_metadata_path(self, name: str) -> str:
        """.lingshu 下的元数据文件路径"""
        return os.path.join(self.lingshu_dir, name)

    def save_metadata(self, name: str, data: dict) -> None:
        path = self.get_metadata_path(name)
        with open(path, "w") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def load_metadata(self, name: str) -> Optional[dict]:
        path = self.get_metadata_path(name)
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
        return None

    def list_task_files(self) -> list[str]:
        """扫描 tasks/ 目录下所有 JSON"""
        tasks_dir = os.path.join(self.root_path, "tasks")
        if not os.path.isdir(tasks_dir):
            return []
        result = []
        for root, _, files in os.walk(tasks_dir):
            for f in sorted(files):
                if f.endswith(".json"):
                    result.append(os.path.join(root, f))
        return result

    @property
    def stats(self) -> dict:
        return {
            "root": self.root_path,
            "lingshu_dir": self.lingshu_dir,
            "is_windows": os.name == "nt",
        }