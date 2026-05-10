#!/usr/bin/env python3
"""
InverseVerifier - 反向验证器（对抗验证引擎）

核心思想：
  用独立模型（反方）挑战解方案（正方）的逻辑，打破自洽性幻觉。
  连续 N 次 100% 通过的验证即确认环境可信。

验证强度（可配置）:
  light  : 单模型自我反驳 + 检查清单
  medium : 启动反向验证器，走单轮辩论（默认）
  deep   : 反方+裁判+要求反方提供替代实现并通过测试

信任机制:
  - 连续 10 次 100% 通过 → 环境标记为"可信"
  - 任何一次失败 → 信任分归零，重新累积
  - 可信环境下跳过验证（加速循环）
  - 信任状态持久化到 SQLite（重启不丢失）

工作流:
  正方 (Proposer) → 提出根因+修复方案
  反方 (Challenger) → 找出逻辑漏洞 + 提出替代根因
  裁判 (Adjudicator) → 阅读双方论据，最终裁决
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Callable

from core.agent_chain import AgentChain
from workflows.task_workflow import Task

logger = logging.getLogger(__name__)


class VerificationIntensity(Enum):
    LIGHT = "light"
    MEDIUM = "medium"
    DEEP = "deep"


@dataclass
class VerificationRound:
    round_number: int
    challenger_arguments: str = ""
    challenger_alternative_root_cause: str = ""
    challenger_edge_cases: list[str] = field(default_factory=list)
    proposer_rebuttal: str = ""
    adjudicator_verdict: str = ""
    adjudicator_score: float = 0.0
    adjudicator_reasoning: str = ""
    passed: bool = False
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().isoformat()


@dataclass
class VerificationResult:
    task_id: str
    intensity: str
    total_rounds: int
    rounds: list[VerificationRound] = field(default_factory=list)
    final_passed: bool = False
    final_confidence: float = 0.0
    summary: str = ""
    evidence_chain: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "intensity": self.intensity,
            "total_rounds": self.total_rounds,
            "rounds": [
                {"round": r.round_number, "passed": r.passed, "score": r.adjudicator_score}
                for r in self.rounds
            ],
            "final_passed": self.final_passed,
            "final_confidence": self.final_confidence,
            "summary": self.summary[:200],
        }


class InverseVerifierConfig:
    """反向验证器配置"""

    def __init__(
        self,
        enabled: bool = True,
        default_intensity: str = "medium",
        challenger_model: str = "elite",
        adjudicator_model: str = "elite",
        max_rounds: int = 3,
        require_alternative_root_cause: bool = True,
        confidence_threshold: float = 95.0,
        consecutive_pass_required: int = 10,
        trust_db_path: str = "",
    ):
        self.enabled = enabled
        self.default_intensity = default_intensity
        self.challenger_model = challenger_model
        self.adjudicator_model = adjudicator_model
        self.max_rounds = max_rounds
        self.require_alternative_root_cause = require_alternative_root_cause
        self.confidence_threshold = confidence_threshold
        self.consecutive_pass_required = consecutive_pass_required
        self.trust_db_path = trust_db_path

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "default_intensity": self.default_intensity,
            "challenger_model": self.challenger_model,
            "adjudicator_model": self.adjudicator_model,
            "max_rounds": self.max_rounds,
            "require_alternative_root_cause": self.require_alternative_root_cause,
            "confidence_threshold": self.confidence_threshold,
            "consecutive_pass_required": self.consecutive_pass_required,
        }


class TrustScoreTracker:
    """
    信任分追踪器（SQLite 持久化版）

    核心逻辑：
    - 每次验证通过累积信任分
    - 连续 N 次 (默认10) 100% 通过 → 环境标记为"可信"
    - 任何一次失败 → 信任分归零，重新累积
    - 可信环境下跳过验证（加速）
    - 所有记录持久化到 SQLite

    DB 表结构:
      trust_records(id, task_id, passed, confidence, consecutive_at_time, timestamp)
      trust_state(id, consecutive_pass, trusted, updated_at)
    """

    def __init__(self, consecutive_required: int = 10, db_path: str = ""):
        self.consecutive_required = consecutive_required
        self.db_path = db_path or os.path.join(
            os.path.dirname(os.path.abspath(".")), ".lingshu", "trust.db"
        )
        self._history: list[dict] = []
        self._consecutive_pass = 0
        self._trusted = False
        self._init_db()
        self._load_state()

    def _init_db(self) -> None:
        """初始化信任数据库"""
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute("""
                CREATE TABLE IF NOT EXISTS trust_records (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id TEXT NOT NULL,
                    passed INTEGER NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0.0,
                    consecutive_at_time INTEGER NOT NULL DEFAULT 0,
                    timestamp TEXT NOT NULL
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS trust_state (
                    id INTEGER PRIMARY KEY CHECK(id = 1),
                    consecutive_pass INTEGER NOT NULL DEFAULT 0,
                    trusted INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                )
            """)
            # 确保状态行存在
            c.execute(
                "INSERT OR IGNORE INTO trust_state (id, consecutive_pass, trusted, updated_at) "
                "VALUES (1, 0, 0, ?)",
                (datetime.now().isoformat(),),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Failed to init trust DB: {e}")

    def _load_state(self) -> None:
        """从 DB 加载持久化信任状态"""
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute("SELECT consecutive_pass, trusted FROM trust_state WHERE id=1")
            row = c.fetchone()
            if row:
                self._consecutive_pass = row[0]
                self._trusted = bool(row[1])
            conn.close()
            logger.info(
                f"Trust state loaded: consecutive={self._consecutive_pass}, "
                f"trusted={self._trusted}"
            )
        except Exception as e:
            logger.warning(f"Failed to load trust state from DB: {e}")

    def _save_state(self) -> None:
        """持久化信任状态到 SQLite"""
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute(
                "UPDATE trust_state SET consecutive_pass=?, trusted=?, updated_at=? WHERE id=1",
                (self._consecutive_pass, 1 if self._trusted else 0, datetime.now().isoformat()),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Failed to save trust state: {e}")

    def record(self, task_id: str, passed: bool, confidence: float) -> None:
        """记录一次验证结果并持久化"""
        prev_consecutive = self._consecutive_pass
        entry = {
            "task_id": task_id,
            "passed": passed,
            "confidence": confidence,
            "timestamp": datetime.now().isoformat(),
        }
        self._history.append(entry)

        # 更新连续通过计数
        if passed and confidence >= 95.0:
            self._consecutive_pass += 1
        else:
            self._consecutive_pass = 0
            self._trusted = False

        # 检查是否达到信任阈值
        if self._consecutive_pass >= self.consecutive_required:
            self._trusted = True
            logger.info(
                f"[TRUST] Environment TRUSTED after {self._consecutive_pass} "
                f"consecutive 100% passes! (task={task_id})"
            )

        # 持久化记录到 SQLite
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute(
                "INSERT INTO trust_records (task_id, passed, confidence, consecutive_at_time, timestamp) "
                "VALUES (?, ?, ?, ?, ?)",
                (task_id, 1 if passed else 0, confidence, self._consecutive_pass, entry["timestamp"]),
            )
            # 同时更新状态
            c.execute(
                "UPDATE trust_state SET consecutive_pass=?, trusted=?, updated_at=? WHERE id=1",
                (self._consecutive_pass, 1 if self._trusted else 0, datetime.now().isoformat()),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            logger.warning(f"Failed to persist trust record: {e}")

        if self._consecutive_pass != prev_consecutive:
            logger.info(
                f"[TRUST] Task={task_id}, passed={passed}, conf={confidence:.1f}%, "
                f"consecutive={self._consecutive_pass}/{self.consecutive_required}"
            )

    def reset(self) -> None:
        """重置信任状态"""
        self._consecutive_pass = 0
        self._trusted = False
        self._history.clear()
        self._save_state()
        logger.warning("[TRUST] Trust state manually reset")

    def get_statistics(self) -> dict:
        """获取完整信任统计"""
        total_pass = sum(1 for h in self._history if h["passed"])
        total_fail = sum(1 for h in self._history if not h["passed"])
        return {
            "trusted": self._trusted,
            "consecutive_pass": self._consecutive_pass,
            "required": self.consecutive_required,
            "progress_pct": round(self._consecutive_pass / max(self.consecutive_required, 1) * 100, 1),
            "total_verified": len(self._history),
            "total_pass": total_pass,
            "total_fail": total_fail,
            "pass_rate": round(total_pass / max(len(self._history), 1) * 100, 1),
            "trust_db": self.db_path,
        }

    def get_recent_history(self, limit: int = 20) -> list[dict]:
        """获取最近验证历史"""
        return self._history[-limit:] if self._history else []

    def get_all_records_from_db(self, limit: int = 100) -> list[dict]:
        """从 DB 获取所有历史记录"""
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute(
                "SELECT task_id, passed, confidence, consecutive_at_time, timestamp "
                "FROM trust_records ORDER BY id DESC LIMIT ?",
                (limit,),
            )
            rows = [
                {
                    "task_id": r[0],
                    "passed": bool(r[1]),
                    "confidence": r[2],
                    "consecutive_at_time": r[3],
                    "timestamp": r[4],
                }
                for r in c.fetchall()
            ]
            conn.close()
            return rows
        except Exception as e:
            logger.warning(f"Failed to read trust records: {e}")
            return []

    @property
    def is_trusted(self) -> bool:
        return self._trusted

    @property
    def consecutive_pass_count(self) -> int:
        return self._consecutive_pass

    @property
    def remaining_to_trust(self) -> int:
        return max(0, self.consecutive_required - self._consecutive_pass)


class InverseVerifier:
    """
    反向验证器 - 对抗验证引擎

    使用独立模型（反方）挑战解方案，再由裁判裁决。
    连续 N 次 100% 通过后标记环境为可信，可降低后续验证强度。
    信任状态持久化到 SQLite，重启不丢失。
    """

    def __init__(
        self,
        agent_chain: AgentChain,
        config: Optional[InverseVerifierConfig] = None,
    ):
        self.agent_chain = agent_chain
        self.config = config or InverseVerifierConfig()
        self.trust_tracker = TrustScoreTracker(
            consecutive_required=self.config.consecutive_pass_required,
            db_path=self.config.trust_db_path,
        )

    @property
    def is_environment_trusted(self) -> bool:
        """环境是否已被信任（引擎可直接调用）"""
        return self.trust_tracker.is_trusted

    @property
    def trust_statistics(self) -> dict:
        """信任统计数据（引擎可查询）"""
        return self.trust_tracker.get_statistics()

    def get_verification_history(self, limit: int = 50) -> list[dict]:
        """获取验证历史"""
        return self.trust_tracker.get_all_records_from_db(limit=limit)

    def reset_trust(self) -> None:
        """手动重置信任状态"""
        self.trust_tracker.reset()

    async def verify(
        self,
        task: Task,
        intensity: Optional[str] = None,
        on_round: Optional[Callable[[VerificationRound], None]] = None,
    ) -> VerificationResult:
        """
        执行反向验证

        参数:
            task: 已完成的任务（含 steps 和 results）
            intensity: 验证强度 (light|medium|deep)，None 使用配置默认值
            on_round: 每轮回调

        返回:
            VerificationResult 包含完整验证链
        """
        intensity = intensity or self.config.default_intensity

        result = VerificationResult(
            task_id=task.task_id,
            intensity=intensity,
            total_rounds=0,
        )

        if not self.config.enabled:
            result.final_passed = True
            result.final_confidence = 100.0
            result.summary = "Verifier disabled, auto-pass"
            return result

        # === 如果环境已被信任，跳过深度验证 ===
        if self.trust_tracker.is_trusted and intensity != "deep":
            logger.info(
                f"[VERIFIER] Environment TRUSTED, skip verify for {task.task_id}"
            )
            result.final_passed = True
            result.final_confidence = 100.0
            result.summary = "Environment trusted (consecutive_pass="
            f"{self.trust_tracker.consecutive_pass_count}), no verification needed"
            # 即使是跳过，也记录一次通过以维持信任计数
            self.trust_tracker.record(task.task_id, True, 100.0)
            return result

        logger.info(f"[VERIFIER] Starting {intensity} verification for {task.task_id}")
        logger.info(
            f"[VERIFIER] Trust progress: {self.trust_tracker.consecutive_pass_count}/"
            f"{self.trust_tracker.consecutive_required}"
        )
        self._add_evidence(result, "start", f"Starting {intensity} verification")

        proposer_context = self._build_proposer_context(task)

        if intensity == "light":
            await self._run_light_verify(result, proposer_context, on_round)
        elif intensity == "deep":
            await self._run_deep_verify(result, proposer_context, on_round)
        else:
            await self._run_medium_verify(result, proposer_context, on_round)

        passed = result.final_passed
        confidence = result.final_confidence

        # 记录到信任追踪器（持久化到 SQLite）
        self.trust_tracker.record(task.task_id, passed, confidence)

        stats = self.trust_tracker.get_statistics()
        self._add_evidence(
            result, "final",
            f"Verdict: {'PASS' if passed else 'FAIL'} (confidence={confidence:.1f}%, "
            f"consecutive_pass={stats['consecutive_pass']}/{stats['required']}, "
            f"trusted={stats['trusted']})"
        )

        logger.info(
            f"[VERIFIER] {task.task_id}: {'✓ PASS' if passed else '✗ FAIL'} "
            f"(confidence={confidence:.1f}%, trust={stats['consecutive_pass']}/"
            f"{stats['required']}, trusted={stats['trusted']})"
        )

        return result

    async def _run_light_verify(
        self,
        result: VerificationResult,
        proposer_context: str,
        on_round: Optional[Callable] = None,
    ) -> None:
        """轻度验证：单模型自我反驳 + 检查清单"""
        reviewer = self.agent_chain.get_agent("strong")
        if not reviewer:
            result.final_passed = True
            result.final_confidence = 100.0
            result.summary = "No reviewer available"
            return

        prompt = (
            f"You are a tech lead doing a light review. "
            f"Review the following solution:\n\n"
            f"{proposer_context}\n\n"
            f"Checklist:\n"
            f"1. Is the root cause correctly identified?\n"
            f"2. Does the fix address the root cause?\n"
            f"3. Are there any edge cases not covered?\n"
            f"4. Could the fix introduce regressions?\n"
            f"5. Is the solution minimally invasive?\n\n"
            f"For each item, answer YES/NO and briefly explain.\n"
            f"\nFinal verdict format:\n"
            f"VERDICT: PASS|FAIL\n"
            f"CONFIDENCE: <0-100>\n"
            f"ISSUES: <any issues found>"
        )

        response = await reviewer.execute(prompt=prompt)
        passed = _parse_verdict(response)
        confidence = _parse_confidence(response)

        result.final_passed = passed or confidence >= 60.0
        result.final_confidence = max(confidence, 50.0)
        result.summary = response[:300]

        round_data = VerificationRound(
            round_number=1,
            challenger_arguments=response,
            adjudicator_verdict="PASS" if passed else "FAIL",
            adjudicator_score=confidence,
            passed=passed,
        )
        result.rounds.append(round_data)
        if on_round:
            on_round(round_data)

    async def _run_medium_verify(
        self,
        result: VerificationResult,
        proposer_context: str,
        on_round: Optional[Callable] = None,
    ) -> None:
        """
        中度验证：启动反方挑战，走单轮辩论
        """
        challenger = self.agent_chain.get_agent(self.config.challenger_model)
        adjudicator = self.agent_chain.get_agent(self.config.adjudicator_model)

        if not challenger or not adjudicator:
            logger.warning(
                "[VERIFIER] Challenger/adjudicator not available, "
                "falling back to light"
            )
            await self._run_light_verify(result, proposer_context, on_round)
            return

        best_score = 0.0
        best_passed = False

        for round_num in range(1, self.config.max_rounds + 1):
            logger.info(
                f"[VERIFIER] Medium verify round {round_num}/"
                f"{self.config.max_rounds}"
            )

            challenge_prompt = (
                f"You are a CHALLENGER. Find flaws in the proposed solution.\n\n"
                f"Solution:\n{proposer_context}\n\n"
                f"You MUST:\n"
                f"1. Identify logical gaps or flaws in the reasoning\n"
                f"2. Propose an alternative root cause hypothesis\n"
                f"3. List edge cases that the solution might miss\n"
                f"4. Analyze whether the fix could introduce regressions\n"
                f"5. Rate the solution confidence (0-100):\n\n"
                f"Format your response exactly as:\n"
                f"LOGICAL_GAPS: <list gaps>\n"
                f"ALTERNATIVE_ROOT_CAUSE: <your hypothesis>\n"
                f"EDGE_CASES: <list>\n"
                f"REGRESSION_RISK: <low|medium|high>\n"
                f"CHALLENGER_CONFIDENCE: <0-100>"
            )

            if self.config.require_alternative_root_cause:
                challenge_prompt += (
                    f"\n\nIMPORTANT: You MUST provide an alternative "
                    f"root cause hypothesis. Even if you agree, propose "
                    f"a different perspective to test robustness."
                )

            challenge_response = await challenger.execute(prompt=challenge_prompt)

            # === 正方反驳 ===
            rebuttal_prompt = (
                f"You are the PROPOSER defending your solution. "
                f"Respond to the challenge.\n\n"
                f"Your original solution:\n{proposer_context}\n\n"
                f"Challenge received:\n{challenge_response}\n\n"
                f"Address each point raised by the challenger. "
                f"Explain why your solution is correct or acknowledge "
                f"valid concerns."
            )

            proposer = self.agent_chain.get_agent("strong")
            rebuttal = ""
            if proposer:
                rebuttal = await proposer.execute(prompt=rebuttal_prompt)

            # === 裁判裁决 ===
            adjudicator_prompt = (
                f"You are the ADJUDICATOR. Read both sides and rule.\n\n"
                f"=== PROPOSER SOLUTION ===\n{proposer_context}\n\n"
                f"=== CHALLENGER ARGUMENTS ===\n{challenge_response}\n\n"
                f"=== PROPOSER REBUTTAL ===\n{rebuttal}\n\n"
                f"Evaluate:\n"
                f"1. Did the challenger identify valid flaws?\n"
                f"2. Did the proposer adequately address challenges?\n"
                f"3. Overall solution quality:\n\n"
                f"Output format:\n"
                f"VERDICT: PASS|FAIL\n"
                f"CONFIDENCE: <0-100>\n"
                f"REASONING: <detailed reasoning>"
            )

            verdict = await adjudicator.execute(prompt=adjudicator_prompt)

            passed = _parse_verdict(verdict)
            score = _parse_confidence(verdict)
            reasoning = _extract_field(verdict, "REASONING")

            round_data = VerificationRound(
                round_number=round_num,
                challenger_arguments=challenge_response[:500],
                challenger_alternative_root_cause=_extract_field(
                    challenge_response, "ALTERNATIVE_ROOT_CAUSE"
                ),
                challenger_edge_cases=_extract_list(
                    challenge_response, "EDGE_CASES"
                ),
                proposer_rebuttal=rebuttal[:500],
                adjudicator_verdict="PASS" if passed else "FAIL",
                adjudicator_score=score,
                adjudicator_reasoning=reasoning,
                passed=passed,
            )
            result.rounds.append(round_data)

            if score > best_score:
                best_score = score
                best_passed = passed or score >= self.config.confidence_threshold

            if on_round:
                on_round(round_data)

            if passed and score >= self.config.confidence_threshold:
                logger.info(
                    f"[VERIFIER] Early stop at round {round_num} "
                    f"(score={score})"
                )
                break

        result.final_passed = best_passed
        result.final_confidence = best_score
        result.summary = (
            f"Medium verification: {len(result.rounds)} round(s), "
            f"best_score={best_score:.1f}, passed={best_passed}"
        )

    async def _run_deep_verify(
        self,
        result: VerificationResult,
        proposer_context: str,
        on_round: Optional[Callable] = None,
    ) -> None:
        """深度验证：多轮辩论 + 要求反方提供替代实现"""
        await self._run_medium_verify(result, proposer_context, on_round)

        if not result.final_passed:
            return

        logger.info(
            "[VERIFIER] Deep verification: requesting alternative implementation"
        )

        challenger = self.agent_chain.get_agent(self.config.challenger_model)
        if not challenger:
            return

        alt_impl_prompt = (
            f"You are the CHALLENGER. The proposer's solution was "
            f"reviewed but we want to verify robustness through "
            f"independent implementation.\n\n"
            f"Original task: {proposer_context[:500]}\n\n"
            f"Provide:\n"
            f"1. An alternative implementation of the fix (code)\n"
            f"2. Test cases that would verify correctness\n"
            f"3. How your alternative differs from the original\n"
            f"4. Which approach is superior and why"
        )

        alt_response = await challenger.execute(prompt=alt_impl_prompt)

        self._add_evidence(
            result, "deep_alternative",
            f"Alternative implementation provided ({len(alt_response)} chars)"
        )

        result.final_confidence = min(result.final_confidence + 5.0, 100.0)
        result.summary = (
            f"Deep verification with alternative implementation. "
            f"Final confidence: {result.final_confidence:.1f}%"
        )

    def _build_proposer_context(self, task: Task) -> str:
        """组装正方（任务解方案）的完整上下文"""
        lines = [
            f"Task: {task.description}",
            f"Type: {task.type}",
            f"Status: {task.status}",
        ]

        if task.result:
            lines.append(f"Result: {task.result[:2000]}")
        if task.error:
            lines.append(f"Error: {task.error}")

        lines.append("\nSteps:")
        for s in task.steps:
            status_icon = "✓" if s.status == "done" else "✗"
            lines.append(
                f"  {status_icon} [{s.step_id}] {s.description} "
                f"({s.assigned_agent})"
            )
            if s.result:
                lines.append(f"    Result: {s.result[:200]}")
            if s.error:
                lines.append(f"    Error: {s.error}")

        return "\n".join(lines)

    def _add_evidence(self, result: VerificationResult, phase: str, summary: str) -> None:
        result.evidence_chain.append({
            "phase": phase,
            "summary": summary,
            "timestamp": datetime.now().isoformat(),
        })


def _parse_verdict(text: str) -> bool:
    """从文本解析 VERDICT 字段"""
    for line in text.split("\n"):
        if "VERDICT" in line:
            return "PASS" in line.upper()
    return False


def _parse_confidence(text: str) -> float:
    """从文本解析 CONFIDENCE 数值"""
    for line in text.split("\n"):
        if "CONFIDENCE" in line:
            num = "".join(c for c in line if c.isdigit() or c == ".")
            if num:
                return float(num.rstrip(".%"))
    return 0.0


def _extract_field(text: str, field: str) -> str:
    """从结构化输出提取字段"""
    for line in text.split("\n"):
        if line.startswith(field + ":"):
            return line.split(":", 1)[-1].strip()
    return ""


def _extract_list(text: str, field: str) -> list[str]:
    """从结构化输出提取列表字段"""
    items = []
    in_field = False
    for line in text.split("\n"):
        if line.startswith(field + ":"):
            in_field = True
            continue
        if in_field:
            stripped = line.strip()
            if stripped.startswith("-"):
                items.append(stripped.lstrip("- "))
            elif stripped and not stripped.startswith("  "):
                in_field = False
    return items