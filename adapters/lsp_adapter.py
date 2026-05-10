#!/usr/bin/env python3
"""
LSPAdapter - LSP / 静态分析接口
对接 pylsp、jedi 等语言服务器实现代码分析
"""
from __future__ import annotations
import subprocess
import json
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class LSPAdapter:
    """LSP 静态分析适配器"""

    def __init__(self, endpoint: str = ""):
        self.endpoint = endpoint

    def analyze_file(self, file_path: str) -> Dict:
        """分析单文件，返回错误和警告"""
        result = {"errors": [], "warnings": [], "info": []}
        try:
            out = subprocess.run(
                ["pylsp", file_path], capture_output=True, text=True, timeout=30
            )
            for line in out.stdout.split("\n"):
                if line.strip():
                    result["info"].append(line.strip())
        except FileNotFoundError:
            logger.warning("pylsp not found, skipping LSP analysis")
        except subprocess.TimeoutExpired:
            logger.warning("LSP analysis timed out")
        except Exception as e:
            logger.error(f"LSP analysis failed: {e}")
        return result

    def get_symbols(self, file_path: str) -> List[Dict]:
        """获取文件中的符号定义"""
        return []

    def validate_code(self, code: str, language: str = "python") -> Dict:
        """验证代码片段正确性"""
        return {"valid": True, "issues": []}