#!/usr/bin/env python3
"""
FeedbackLearner - 隐式反馈学习器 + 显式 teach 接口

职责:
  1. 追踪用户每一次操作行为的隐式反馈（命令、参数、结果）
  2. 分析模式，自动生成画像规则
  3. 提供 `teach` 接口："teach X that Y" 直接学习
  4. 驱动个人画像的持续进化（memory → profile）

核心循环:
  user_action → feedback_learner.record() → _analyze() → _evolve_profile()
"""
from __future__ import annotations
import json
import logging
import os
import re
import time
from collections import defaultdict, Counter
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Any

from memory.short_term import ShortTermMemory
from memory.mid_term import MidTermMemory
from core.profile_loader import ProfileLoader

try:
    from rich.console import Console
    _console = Console()
except ImportError:
    _console = None

logger = logging.getLogger(__name__)


@dataclass
class ActionRecord:
    """单次用户行为记录"""
    action_type: str          # command | teach | edit | review_feedback | reject
    key: str                  # 行为标识（如 "run", "batch", "--fast"）
    detail: str = ""          # 附加描述
    duration_ms: float = 0.0
    success: bool = True
    timestamp: str = ""
    context: dict = field(default_factory=dict)

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


@dataclass
class ObservedPattern:
    """从用户行为中观察到的模式"""
    pattern_key: str          # 模式键名
    frequency: int = 1        # 出现次数
    confidence: float = 0.0   # 置信度 0-1
    last_observed: str = ""
    examples: list = field(default_factory=list)

    def __post_init__(self):
        if not self.last_observed:
            self.last_observed = datetime.now().isoformat()


class FeedbackLearner:
    """
    隐式反馈学习器

    自动从用户每次操作中学习偏好和模式，持续进化个人画像。
    """

    def __init__(
        self,
        profile_loader: Optional[ProfileLoader] = None,
        short_term: Optional[ShortTermMemory] = None,
        mid_term: Optional[MidTermMemory] = None,
        storage_dir: str = ".lingshu",
    ):
        self.profile_loader = profile_loader or ProfileLoader()
        self.short_term = short_term or ShortTermMemory()
        self.mid_term = mid_term or MidTermMemory()
        self.storage_dir = Path(storage_dir)

        # 行为追踪
        self._action_log: list[ActionRecord] = []
        self._patterns: dict[str, ObservedPattern] = {}
        self._command_counter: Counter = Counter()
        self._flag_counter: Counter = Counter()
        self._reject_counter: Counter = Counter()
        self._max_log_size = 1000

        # 隐式学习阈值
        self._min_frequency_for_pattern = 3   # 至少出现 3 次才形成模式
        self._min_confidence_for_evolve = 0.6  # 至少 60% 才进化到画像

        # 加载历史
        self._load_history()

    # ====== 公开接口 ======

    def record(
        self,
        action_type: str,
        key: str,
        detail: str = "",
        duration_ms: float = 0.0,
        success: bool = True,
        context: Optional[dict] = None,
    ) -> None:
        """
        记录一次用户行为（隐式反馈的原始输入）

        参数:
            action_type: 行为类型 (command|teach|edit|review_feedback|reject)
            key: 行为标识
            detail: 附加描述
            duration_ms: 耗时
            success: 是否成功
            context: 上下文信息
        """
        record = ActionRecord(
            action_type=action_type,
            key=key,
            detail=detail[:500],
            duration_ms=duration_ms,
            success=success,
            context=context or {},
        )
        self._action_log.append(record)
        logger.debug(f"Feedback record: {action_type}/{key} success={success}")

        # 更新计数器
        if action_type == "command":
            self._command_counter[key] += 1
        elif action_type == "reject":
            self._reject_counter[key] += 1
            # 记录到中期记忆
            self.mid_term.record_decision(
                context=f"user_rejected_{key}",
                decision="reject",
                rationale=detail or f"User rejected {key}",
            )

        # 检测标志使用
        if context and "flags" in context:
            for flag in context["flags"]:
                self._flag_counter[flag] += 1

        # 限制日志大小
        if len(self._action_log) > self._max_log_size:
            self._action_log = self._action_log[-self._max_log_size:]

        # 每 20 条记录自动分析一次模式
        if len(self._action_log) % 20 == 0:
            self._analyze_patterns()

        # 持久化
        self._save_history()

    def teach(self, rule_key: str, rule_value: str, scope: str = "project") -> dict:
        """
        显式 teach 接口：直接教会 AI 一条偏好规则

        用法:
            learner.teach("preferred_language", "Rust")
            learner.teach("disliked_patterns", "全局变量", scope="global")

        返回:
            dict 包含 teach 结果
        """
        self.profile_loader.add_rule(rule_key, rule_value, scope=scope)

        # 记录到记忆
        self.mid_term.record_decision(
            context=f"teach_{rule_key}",
            decision=f"{rule_key} = {rule_value}",
            rationale=f"User explicitly taught: {rule_key} is {rule_value}",
        )

        # 也记录为反馈
        self.record(
            action_type="teach",
            key=f"teach_{rule_key}",
            detail=f"{rule_key} = {rule_value} ({scope})",
            success=True,
        )

        target = ".lingshu/profile.yaml" if scope == "project" else "~/.lingshu/profile.yaml"

        logger.info(f"Teached: {rule_key} = {rule_value} -> {target}")

        if _console:
            _console.print(f"[green]OK: {rule_key} = {rule_value}[/green]")
            _console.print(f"   [dim]写入: {target}[/dim]")

        return {
            "success": True,
            "rule_key": rule_key,
            "rule_value": rule_value,
            "scope": scope,
            "target": target,
        }

    def teach_from_text(self, text: str, scope: str = "project") -> dict:
        """
        从自然语言解析 teach 指令

        支持格式:
            "teach preferred_language = Rust"
            "teach coding_style = 函数式优先"
            "我不喜欢魔法数字" → 自动映射到 disliked_patterns
            "我更喜欢 FastAPI" → 自动映射到 favorite_tools
        """
        text = text.strip()

        # 格式 1: "teach key = value"
        teach_match = re.match(
            r'^teach\s+(\w+)\s*[=:]\s*(.+)$', text, re.IGNORECASE
        )
        if teach_match:
            return self.teach(teach_match.group(1), teach_match.group(2).strip(), scope)

        # 格式 2: "我不喜欢 X" → disliked_patterns
        dislike_match = re.match(
            r'(?:我)?不?喜欢(.+)$', text
        )
        if dislike_match:
            value = dislike_match.group(1).strip()
            existing = self.profile_loader.get("disliked_patterns", [])
            if isinstance(existing, list) and value not in existing:
                return self.teach("disliked_patterns", value, scope)
            return {"success": True, "note": f"already known: {value}"}

        # 格式 3: "我更喜欢 X" → favorite_tools
        prefer_match = re.match(
            r'(?:我)?更喜欢(.+)$', text
        )
        if prefer_match:
            value = prefer_match.group(1).strip()
            existing = self.profile_loader.get("favorite_tools", [])
            if isinstance(existing, list) and value not in existing:
                return self.teach("favorite_tools", value, scope)
            return {"success": True, "note": f"already known: {value}"}

        # 格式 4: "key = value"（无 teach 前缀）
        kv_match = re.match(r'^(\w+)\s*[=:]\s*(.+)$', text)
        if kv_match:
            return self.teach(kv_match.group(1), kv_match.group(2).strip(), scope)

        # 无法解析
        logger.warning(f"Cannot parse teach text: {text}")
        return {
            "success": False,
            "error": f"无法解析: {text}。请用格式：teach key = value",
        }

    def evolve_profile(self) -> dict:
        """
        从观察到的模式进化个人画像

        扫描所有高置信度模式，自动写入画像文件。
        """
        self._analyze_patterns()
        evolved = []

        # 1. 常用标志 → 默认策略
        if self._flag_counter:
            most_common_flag = self._flag_counter.most_common(1)[0]
            flag_name, flag_count = most_common_flag
            if flag_count >= self._min_frequency_for_pattern:
                strategy_map = {
                    "--fast": "fast",
                    "--deep": "deep",
                }
                if flag_name in strategy_map:
                    current = self.profile_loader.get("default_strategy", "")
                    new_val = strategy_map[flag_name]
                    if current != new_val:
                        self.profile_loader.add_rule("default_strategy", new_val)
                        evolved.append(f"default_strategy={new_val} (from flag {flag_name} x{flag_count})")

        # 2. 频繁拒绝的模式 → disliked_patterns
        for key, count in self._reject_counter.most_common(5):
            if count >= self._min_frequency_for_pattern:
                # 将拒绝的关键词转为 disliked_patterns
                existing = self.profile_loader.get("disliked_patterns", [])
                if isinstance(existing, list) and key not in existing:
                    self.profile_loader.add_rule("disliked_patterns", key)
                    evolved.append(f"disliked_patterns+={key} (rejected x{count})")

        # 3. 高频命令 → 推断能力偏好
        if self._command_counter:
            top_cmds = self._command_counter.most_common(3)
            cmd_names = [c[0] for c in top_cmds if c[1] >= self._min_frequency_for_pattern]
            if cmd_names:
                current = self.profile_loader.get("frequent_commands", [])
                if isinstance(current, list):
                    for cmd in cmd_names:
                        if cmd not in current:
                            self.profile_loader.add_rule("frequent_commands", cmd)
                            evolved.append(f"frequent_commands+={cmd}")

        # 4. 高置信度模式 → 画像
        for pattern_key, pattern in self._patterns.items():
            if pattern.confidence >= self._min_confidence_for_evolve:
                current_val = self.profile_loader.get(pattern_key, None)
                if current_val is None or current_val != pattern_key:
                    # 生成建议值
                    if pattern_key.startswith("prefer_"):
                        self.profile_loader.add_rule(pattern_key, pattern_key.replace("prefer_", ""))
                        evolved.append(f"{pattern_key} (confidence={pattern.confidence:.0%})")

        if evolved:
            logger.info(f"Profile evolved: {len(evolved)} new rules")
            for e in evolved:
                logger.info(f"  [+] {e}")
        else:
            logger.info("No new patterns to evolve (need more data)")

        return {
            "evolved": len(evolved),
            "rules": evolved,
            "total_patterns": len(self._patterns),
        }

    def get_stats(self) -> dict:
        """获取学习统计"""
        return {
            "total_actions": len(self._action_log),
            "command_counts": dict(self._command_counter.most_common(10)),
            "flag_counts": dict(self._flag_counter.most_common(10)),
            "reject_counts": dict(self._reject_counter.most_common(10)),
            "patterns": {
                k: {"frequency": v.frequency, "confidence": v.confidence}
                for k, v in self._patterns.items()
            },
            "profile": self.profile_loader.profile,
        }

    def get_recent_actions(self, n: int = 20) -> list[dict]:
        """获取最近的用户行为记录"""
        return [asdict(r) for r in self._action_log[-n:]]

    def get_evolve_suggestions(self) -> list[dict]:
        """
        获取建议进化的画像规则（未达到阈值但值得注意的模式）

        供 AI 主动推荐："我发现你经常使用 --fast，是否要设为默认？"
        """
        suggestions = []

        # 标志建议
        for flag, count in self._flag_counter.most_common(5):
            if self._min_frequency_for_pattern > count >= 2:
                strategy_map = {"--fast": "fast", "--deep": "deep"}
                if flag in strategy_map:
                    suggestions.append({
                        "type": "flag_to_strategy",
                        "from_flag": flag,
                        "to_value": strategy_map[flag],
                        "count": count,
                        "message": f"你使用了 {flag} {count} 次，是否设为默认策略？",
                    })

        # 拒绝模式建议
        for key, count in self._reject_counter.most_common(5):
            if self._min_frequency_for_pattern > count >= 2:
                suggestions.append({
                    "type": "reject_to_dislike",
                    "pattern": key,
                    "count": count,
                    "message": f"你拒绝了 {key} {count} 次，是否加入 disliked_patterns？",
                })

        return suggestions

    # ====== 内部方法 ======

    def _analyze_patterns(self) -> None:
        """
        分析用户行为日志，识别可重复的模式

        分析维度:
        1. 命令序列模式（先 run 后 batch）
        2. 标志偏好（--fast vs --deep）
        3. 拒绝模式
        4. 成功/失败比
        5. 时间模式（总是在特定时段做某事）
        """
        if len(self._action_log) < self._min_frequency_for_pattern:
            return

        recent = self._action_log[-100:]

        # === 分析：命令序列模式 ===
        cmd_sequence = [r.key for r in recent if r.action_type == "command"]
        if len(cmd_sequence) >= 3:
            # 检测重复的二元组
            pairs = Counter(zip(cmd_sequence, cmd_sequence[1:]))
            for (a, b), count in pairs.most_common(3):
                if count >= 2:
                    key = f"cmd_pair_{a}_{b}"
                    if key not in self._patterns:
                        self._patterns[key] = ObservedPattern(
                            pattern_key=key,
                            frequency=count,
                            confidence=min(count / 10, 0.9),
                            examples=[f"{a} → {b}"],
                        )
                    else:
                        p = self._patterns[key]
                        p.frequency = count
                        p.confidence = min(count / 10, 0.9)
                        if f"{a} → {b}" not in p.examples:
                            p.examples.append(f"{a} → {b}")

        # === 分析：标志偏好 ===
        total_flags = sum(self._flag_counter.values())
        if total_flags >= self._min_frequency_for_pattern:
            for flag, count in self._flag_counter.most_common(5):
                confidence = min(count / total_flags, 1.0)
                if confidence >= 0.4:  # 40% 以上使用率才算偏好
                    key = f"prefer_flag_{flag.lstrip('-')}"
                    self._patterns[key] = ObservedPattern(
                        pattern_key=key,
                        frequency=count,
                        confidence=confidence,
                        examples=[f"Used {flag} {count}/{total_flags} times"],
                    )

        # === 分析：拒绝模式 ===
        total_actions = len(recent)
        reject_count = sum(1 for r in recent if r.action_type == "reject")
        if reject_count >= self._min_frequency_for_pattern and total_actions > 0:
            reject_ratio = reject_count / total_actions
            if reject_ratio > 0.3:  # 拒绝率超过 30%
                self._patterns["high_reject_rate"] = ObservedPattern(
                    pattern_key="high_reject_rate",
                    frequency=reject_count,
                    confidence=min(reject_ratio, 1.0),
                    examples=[f"Rejected {reject_count}/{total_actions} actions"],
                )

        # === 分析：执行时间模式 ===
        durations = [r.duration_ms for r in recent if r.duration_ms > 0]
        if durations:
            avg_duration = sum(durations) / len(durations)
            if avg_duration > 30000:  # 平均超过 30s
                self._patterns["slow_execution"] = ObservedPattern(
                    pattern_key="slow_execution",
                    frequency=len(durations),
                    confidence=min(avg_duration / 120000, 1.0),
                    examples=[f"Avg duration: {avg_duration/1000:.1f}s"],
                )

    # ====== 持久化 ======

    def _save_history(self) -> None:
        """持久化反馈历史"""
        try:
            self.storage_dir.mkdir(parents=True, exist_ok=True)
            path = self.storage_dir / "feedback_history.json"

            # 只保存最近的 200 条 + 模式 + 计数器
            data = {
                "actions": [asdict(r) for r in self._action_log[-200:]],
                "patterns": {
                    k: {
                        "frequency": v.frequency,
                        "confidence": v.confidence,
                        "last_observed": v.last_observed,
                        "examples": v.examples[-3:],
                    }
                    for k, v in self._patterns.items()
                },
                "counters": {
                    "commands": dict(self._command_counter.most_common(50)),
                    "flags": dict(self._flag_counter.most_common(20)),
                    "rejects": dict(self._reject_counter.most_common(20)),
                },
                "updated_at": datetime.now().isoformat(),
            }

            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.debug(f"Failed to save feedback history: {e}")

    def _load_history(self) -> None:
        """加载持久化的反馈历史"""
        path = self.storage_dir / "feedback_history.json"
        if not path.exists():
            return

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            # 恢复模式
            for key, pdata in data.get("patterns", {}).items():
                self._patterns[key] = ObservedPattern(
                    pattern_key=key,
                    frequency=pdata.get("frequency", 1),
                    confidence=pdata.get("confidence", 0.0),
                    last_observed=pdata.get("last_observed", ""),
                    examples=pdata.get("examples", []),
                )

            # 恢复计数器
            counters = data.get("counters", {})
            self._command_counter.update(counters.get("commands", {}))
            self._flag_counter.update(counters.get("flags", {}))
            self._reject_counter.update(counters.get("rejects", {}))

            # 恢复最近的 action 记录
            for adata in data.get("actions", []):
                self._action_log.append(ActionRecord(**adata))

            logger.info(
                f"Loaded feedback history: {len(self._action_log)} actions, "
                f"{len(self._patterns)} patterns"
            )
        except Exception as e:
            logger.warning(f"Failed to load feedback history: {e}")

    def reset_history(self) -> None:
        """重置所有反馈历史（设计时/测试用）"""
        self._action_log.clear()
        self._patterns.clear()
        self._command_counter.clear()
        self._flag_counter.clear()
        self._reject_counter.clear()
        self._save_history()
        logger.info("Feedback history reset")


# ====== 便捷工具函数 ======

def format_teach_result(result: dict) -> str:
    """格式化 teach 结果供 CLI 显示"""
    if result.get("success"):
        return f"OK: {result['rule_key']} = {result['rule_value']} ({result['scope']})"
    return f"Failed: {result.get('error', 'Unknown error')}"