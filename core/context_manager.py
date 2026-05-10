#!/usr/bin/env python3
"""
ContextManager - 4 级渐进压缩上下文引擎

策略:
  Level 0: 无压缩（保留全部）
  Level 1: 轻度修剪 — 合并相邻 user↔assistant 轮次，截断超长消息(>4000字)
  Level 2: 摘要合并 — 旧轮做摘要（保留最近 12 条）
  Level 3: 激进压缩 — 丢弃 tool 消息，更激进截断

Token 估算:
  CJK ≈ 1.8 token/字
  英文 ≈ 0.25 token/字符
  混合 ≈ 1.0 token/字符

提醒系统:
  会话 >30 分钟 / 任务 >3 个 / 上下文 >80% / 压缩 >3 次
"""
from __future__ import annotations
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field
import time
import logging

logger = logging.getLogger(__name__)


@dataclass
class ContextMessage:
    """单条上下文消息"""
    role: str  # user | assistant | system | tool
    content: str
    meta: Dict = field(default_factory=dict)
    timestamp: float = 0.0


@dataclass
class SessionStats:
    """会话统计"""
    task_count: int = 0
    compression_count: int = 0
    total_messages: int = 0
    start_time: float = 0.0
    last_compression_time: float = 0.0


class ContextManager:
    """无限上下文引擎"""

    # 压缩阈值（占模型 context_window 的比例）
    COMPRESS_THRESHOLD = 0.85  # 85% 时触发压缩
    SAFETY_MARGIN = 0.15  # 压缩到 85%

    # 压缩等级
    LEVEL_NONE = 0
    LEVEL_LIGHT = 1
    LEVEL_MODERATE = 2
    LEVEL_AGGRESSIVE = 3

    def __init__(self, context_window: int = 128000):
        self.context_window = context_window
        self.messages: List[ContextMessage] = []
        self.stats = SessionStats(start_time=time.time())
        self.current_task_id: Optional[str] = None

    def estimate_tokens(self, text: str) -> int:
        """估算文本的 token 数"""
        cjk = sum(1 for c in text if '\u4e00' <= c <= '\u9fff' or '\u3040' <= c <= '\u30ff' or '\uac00' <= c <= '\ud7a3')
        total_len = len(text)
        if cjk > total_len * 0.3:
            return int(cjk * 1.8 + (total_len - cjk) * 0.35)
        return int(total_len * 0.35)

    def total_tokens(self) -> int:
        """当前上下文总 token 数"""
        return sum(self.estimate_tokens(msg.content) for msg in self.messages)

    def add_message(self, role: str, content: str, meta: Optional[Dict] = None) -> None:
        """添加消息到上下文"""
        self.messages.append(ContextMessage(
            role=role, content=content, meta=meta or {}, timestamp=time.time()
        ))
        self.stats.total_messages = len(self.messages)

    def start_new_task(self, task_id: str) -> None:
        """新任务开始，记录任务边界"""
        self.current_task_id = task_id
        self.stats.task_count += 1
        self.add_message("system", f"[Task Boundary: {task_id}]", meta={"type": "task_boundary"})

    def get_compression_level(self) -> int:
        """根据上下文使用率决定压缩等级"""
        usage = self.total_tokens() / self.context_window
        if usage < self.COMPRESS_THRESHOLD:
            return self.LEVEL_NONE
        if self.stats.compression_count < 2:
            return self.LEVEL_LIGHT
        if self.stats.compression_count < 5:
            return self.LEVEL_MODERATE
        return self.LEVEL_AGGRESSIVE

    def compress(self) -> int:
        """执行上下文压缩，返回压缩掉的 token 数"""
        before = self.total_tokens()
        level = self.get_compression_level()

        if level == self.LEVEL_NONE:
            return 0

        target = int(self.context_window * self.SAFETY_MARGIN)
        messages = self.messages

        if level >= self.LEVEL_LIGHT:
            # 合并相邻 user/assistant 轮次
            merged: List[ContextMessage] = []
            for msg in messages:
                if merged and merged[-1].role == msg.role and msg.role in ("user", "assistant"):
                    merged[-1].content += "\n" + msg.content
                else:
                    merged.append(msg)
            messages = merged

        if level >= self.LEVEL_LIGHT:
            # 截断超长消息 (>4000 字符)
            for msg in messages:
                if len(msg.content) > 4000:
                    msg.content = msg.content[:2000] + "\n...[truncated]...\n" + msg.content[-500:]

        if level >= self.LEVEL_MODERATE:
            # 保留最近 12 条完整，旧的做摘要占位
            keep = 12
            if len(messages) > keep:
                summary_count = len(messages) - keep
                messages = messages[summary_count:]
                # 插入摘要标记
                messages.insert(0, ContextMessage(
                    role="system",
                    content=f"[Summary: {summary_count} earlier messages compressed]",
                    meta={"type": "compression_summary"}
                ))

        if level >= self.LEVEL_AGGRESSIVE:
            # 丢弃 tool 消息
            messages = [m for m in messages if m.role != "tool"]
            # 进一步截断
            for msg in messages:
                if len(msg.content) > 2000:
                    msg.content = msg.content[:1000] + "\n...[truncated]...\n"

        self.messages = messages
        self.stats.compression_count += 1
        self.stats.last_compression_time = time.time()

        after = self.total_tokens()
        saved = before - after
        logger.info(f"Context compressed: level={level}, saved={saved}tokens ({before}→{after})")
        return saved

    def get_reminder(self) -> Optional[Dict]:
        """检查是否需要提醒用户开新窗口"""
        reasons = []
        elapsed = time.time() - self.stats.start_time

        if elapsed > 1800:  # 30 分钟
            reasons.append(f"session_duration={int(elapsed/60)}min")
        if self.stats.task_count > 3:
            reasons.append(f"task_count={self.stats.task_count}")
        if self.total_tokens() > self.context_window * 0.8:
            reasons.append(f"context_usage={self.total_tokens()/self.context_window:.0%}")
        if self.stats.compression_count > 3:
            reasons.append(f"compressions={self.stats.compression_count}")

        if reasons:
            return {
                "type": "suggest_new_session",
                "reasons": reasons,
                "suggestion": "Consider starting a new session for better performance",
            }
        return None

    def build_worker_context(self) -> List[Dict]:
        """构建给 Light Agent 的精简上下文"""
        result = []
        for msg in self.messages[-20:]:  # 只取最近 20 条
            if msg.role != "tool":
                result.append({"role": msg.role, "content": msg.content})
        return result

    def prepare_messages(self, system_prompt: str,
                         force_no_compress: bool = False) -> List[Dict]:
        """准备发送给模型的消息列表"""
        if not force_no_compress and self.total_tokens() > self.context_window * self.COMPRESS_THRESHOLD:
            self.compress()

        result = [{"role": "system", "content": system_prompt}]
        for msg in self.messages:
            result.append({"role": msg.role, "content": msg.content})
        return result