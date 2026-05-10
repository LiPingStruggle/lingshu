#!/usr/bin/env python3
"""
ProfileLoader - 用户画像自动注入

职责:
  - 读取 ~/.lingshu/profile.yaml 和项目 .lingshu/profile.yaml
  - 合并（项目覆盖全局）
  - 生成系统提示词注入到 Agent 工作流
  - 监听隐式反馈，自动生成新规则
"""
from __future__ import annotations
import logging
import os
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

logger = logging.getLogger(__name__)

DEFAULT_PROFILE = {
    "language": "中文交流",
    "coding_style": "追求简洁、可读性优先",
    "favorite_tools": [],
    "disliked_patterns": ["魔法数字", "过度设计"],
}


class ProfileLoader:
    """用户画像加载器"""

    def __init__(self, project_path: str = ""):
        self.project_path = project_path or os.getcwd()
        self._profile: dict = {}
        self._loaded = False

    def load(self) -> dict:
        """加载并合并用户画像"""
        if self._loaded:
            return self._profile

        merged = dict(DEFAULT_PROFILE)

        # 1. 全局画像 ~/.lingshu/profile.yaml
        global_path = Path.home() / ".lingshu" / "profile.yaml"
        if global_path.exists():
            try:
                with open(global_path, encoding="utf-8") as f:
                    global_profile = yaml.safe_load(f) if yaml else {}
                    if global_profile:
                        merged.update(global_profile)
                        logger.info(f"Loaded global profile: {global_path}")
            except Exception as e:
                logger.warning(f"Failed to load global profile: {e}")

        # 2. 项目画像 .lingshu/profile.yaml
        project_path = Path(self.project_path) / ".lingshu" / "profile.yaml"
        if project_path.exists():
            try:
                with open(project_path, encoding="utf-8") as f:
                    project_profile = yaml.safe_load(f) if yaml else {}
                    if project_profile:
                        merged.update(project_profile)  # 项目覆盖全局
                        logger.info(f"Loaded project profile: {project_path}")
            except Exception as e:
                logger.warning(f"Failed to load project profile: {e}")

        self._profile = merged
        self._loaded = True
        logger.debug(f"Profile merged: {len(merged)} keys")
        return merged

    def to_system_prompt(self) -> str:
        """将画像转换为系统提示词片段"""
        profile = self.load()
        lines = ["以下是你对当前用户的了解："]

        for key, value in profile.items():
            if isinstance(value, list) and value:
                lines.append(f"- {key}: {', '.join(str(v) for v in value)}")
            elif isinstance(value, str) and value:
                lines.append(f"- {key}: {value}")

        return "\n".join(lines)

    def get(self, key: str, default=None):
        return self.load().get(key, default)

    @property
    def profile(self) -> dict:
        return self.load()

    def add_rule(self, key: str, value: str, scope: str = "project") -> None:
        """动态添加新画像规则"""
        profile = self.load()
        if key not in profile:
            profile[key] = []
        if isinstance(profile[key], list):
            if value not in profile[key]:
                profile[key].append(value)
        else:
            profile[key] = value

        # 写回文件
        if scope == "project":
            target = Path(self.project_path) / ".lingshu" / "profile.yaml"
        else:
            target = Path.home() / ".lingshu" / "profile.yaml"

        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(target, "w", encoding="utf-8") as f:
                if yaml:
                    yaml.dump(profile, f, allow_unicode=True, default_flow_style=False)
                else:
                    f.write(str(profile))
            logger.info(f"Profile rule added: {key}={value} -> {target}")
        except Exception as e:
            logger.error(f"Failed to write profile: {e}")


# 预置模板 - 首次使用生成
PROFILE_TEMPLATE = """# 灵枢用户画像
# 修改此文件以让 AI 更了解你的偏好
# 全局: ~/.lingshu/profile.yaml
# 项目: .lingshu/profile.yaml（覆盖全局）

language: "中文交流"
coding_style: "函数式优先，避免 class"
favorite_tools:
  - FastAPI
  - SQLAlchemy
disliked_patterns:
  - 魔法数字
  - 过度设计
  - 深层嵌套
"""


def ensure_default_profile(project_path: str = "") -> None:
    """如果画像文件不存在，生成默认模板"""
    base = Path(project_path or os.getcwd()) / ".lingshu"
    target = base / "profile.yaml"
    if not target.exists():
        base.mkdir(parents=True, exist_ok=True)
        target.write_text(PROFILE_TEMPLATE, encoding="utf-8")
        logger.info(f"Default profile created: {target}")

    # 全局也确保存在
    global_target = Path.home() / ".lingshu" / "profile.yaml"
    if not global_target.exists():
        global_target.parent.mkdir(parents=True, exist_ok=True)
        global_target.write_text(PROFILE_TEMPLATE, encoding="utf-8")
        logger.info(f"Default global profile created: {global_target}")