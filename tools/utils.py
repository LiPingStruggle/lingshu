#!/usr/bin/env python3
"""
Utils - 通用工具函数
"""
from __future__ import annotations
import json
import os
from typing import Dict, Any


def load_json(path: str) -> Dict[str, Any]:
    """加载 JSON 文件"""
    with open(path) as f:
        return json.load(f)


def save_json(path: str, data: Any) -> None:
    """保存 JSON 文件"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def truncate(text: str, max_len: int = 200) -> str:
    """截断文本"""
    if len(text) <= max_len:
        return text
    return text[:max_len] + "..."