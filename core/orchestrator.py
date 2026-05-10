#!/usr/bin/env python3
"""
Orchestrator - 主编排器（DAG 流水线 / 智能调度 双模式）

模式 A - DAG 流水线 (mode=task|analysis|cascade|debate):
  阶段 0: ConfirmDemand  - 需求自我确认（自问自答 + 用户确认）
  阶段 1: RootCause     - 证据驱动根因分析（EvidenceBasedRootCausePipeline 引擎）
  阶段 2: Decompose     - 任务拆解（唯一 ID + 依赖关系）
  阶段 3: Execute       - 子任务执行（Light Agent，失败重试/升级）
  阶段 4: Review+Patch  - 全局复核（差异校验 + 质量评分 + 回退）

模式 B - 智能调度 (mode=smart):
  委托 IntelligentOrchestrator（LangGraph StateGraph）自动完成:
  1. task_analyzer —— 评估复杂度，自动选择本地/云端模型
  2. resource_allocator —— 按可用性 + 成本分配 Planner + Workers
  3. plan_decomposer —— 拆解为原子性子任务
  4. parallel_executor —— asyncio.gather 多 Worker 并发执行，失败降级
  5. review_and_debate —— 实事求是审查 + 辩论闭环
  6. checkpoint_saver —— SQLite 检查点持久化
  7. final_synthesizer —— 综合输出 Markdown 报告

关键机制:
- 每阶段生成证据链 → 持久化 SQLite
- 阶段间阻塞（必须前序完成才能进入后续）
- 自动循环重试 + 模型升级 + 断点恢复
- 全量可追溯审计
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Optional, Callable, Any

from core.agent_chain import AgentChain, Agent
from core.pipeline import Pipeline
from core.model_router import ModelRouter
from core.error_recovery import ErrorRecovery
from core.context_manager import ContextManager
from core.resource_monitor import ResourceMonitor
from core.inverse_verifier import InverseVerifier, InverseVerifierConfig
from core.smart_references import IntentParser, ParsedIntent
from core.profile_loader import ProfileLoader, ensure_default_profile
from core.feedback_learner import FeedbackLearner
from core.orchestration_core import IntelligentOrchestrator, OrchestratorConfig, ModelPoolConfig
from core.root_cause_pipeline import EvidenceBasedRootCausePipeline
from memory.short_term import ShortTermMemory
from memory.mid_term import MidTermMemory
from workflows.task_workflow import Task, TaskStep

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table
    _console = Console()
except ImportError:
    _console = None

logger = logging.getLogger(__name__)


class EvidenceChain:
    """证据链 - 每阶段持久化审计追踪"""

    def __init__(self, pipeline: Optional[Pipeline] = None):
        self.pipeline = pipeline
        self._entries: list[dict] = []

    def add(self, phase: str, summary: str, detail: str = "",
            evidence: Optional[list] = None) -> None:
        """添加一条证据链记录"""
        entry = {
            "phase": phase,
            "summary": summary,
            "detail": detail,
            "evidence": evidence or [],
            "timestamp": datetime.now().isoformat(),
        }
        self._entries.append(entry)
        logger.info(f"[EVIDENCE] {phase}: {summary[:80]}")

        # 持久化到 SQLite 检查点
        if self.pipeline:
            try:
                self.pipeline.save_checkpoint(
                    task_id=f"evidence_{phase}",
                    step_id=None,
                    phase=phase,
                    status="recorded",
                    output=json.dumps(entry, ensure_ascii=False),
                )
            except Exception as e:
                logger.warning(f"Failed to persist evidence: {e}")

    def get_report(self) -> list[dict]:
        """获取完整证据链报告"""
        return self._entries

    def get_phase_summary(self, phase: str) -> Optional[dict]:
        """获取指定阶段的证据摘要"""
        for e in reversed(self._entries):
            if e["phase"] == phase:
                return e
        return None

    def to_dict(self) -> dict:
        return {"evidence_chain": self._entries, "count": len(self._entries)}


class Orchestrator:
    """
    主编排器 - DAG 流水线 + 智能调度 双模式

    模式 A - DAG 流水线：6 阶段精确阻塞式流水线
    模式 B - 智能调度：IntelligentOrchestrator LangGraph 自动调度
    """

    def __init__(
        self,
        agent_chain: Optional[AgentChain] = None,
        pipeline: Optional[Pipeline] = None,
        model_router: Optional[ModelRouter] = None,
        error_recovery: Optional[ErrorRecovery] = None,
        context_manager: Optional[ContextManager] = None,
        resource_monitor: Optional[ResourceMonitor] = None,
        intent_parser: Optional[IntentParser] = None,
        profile_loader: Optional[ProfileLoader] = None,
        intelligent_orchestrator: Optional[IntelligentOrchestrator] = None,
        root_cause_pipeline: Optional[EvidenceBasedRootCausePipeline] = None,
    ):
        self.agent_chain = agent_chain or AgentChain()
        self.pipeline = pipeline
        self.model_router = model_router or ModelRouter()
        self.error_recovery = error_recovery or ErrorRecovery(model_router)
        self.context_manager = context_manager or ContextManager()
        self.resource_monitor = resource_monitor or ResourceMonitor()
        self.evidence = EvidenceChain(pipeline=pipeline)

        self.profile_loader = profile_loader or ProfileLoader()
        self._skip_confirmation = False
        ensure_default_profile()
        self.intent_parser = intent_parser or IntentParser()

        # 初始化反馈学习器
        self.feedback_learner = FeedbackLearner(
            profile_loader=self.profile_loader,
            short_term=ShortTermMemory(),
            mid_term=MidTermMemory(),
        )

        # 初始化默认模型
        self.model_router.register_default_models()

        # 初始化反向验证器并注入 pipeline
        self.inverse_verifier = InverseVerifier(self.agent_chain)
        if self.pipeline:
            self.pipeline.inverse_verifier = self.inverse_verifier

        # 初始化智能调度总控（模式 B）
        self.intelligent_orchestrator = intelligent_orchestrator or IntelligentOrchestrator()

        # 初始化证据驱动根因分析管道（阶段 1 增强）
        self.root_cause_pipeline = root_cause_pipeline

        # 注入画像提示词到 agent_chain
        self._inject_profile()

    def _inject_profile(self) -> None:
        """将用户画像注入到 Agent system prompt 的最前面"""
        try:
            profile_text = self.profile_loader.to_system_prompt()
            if profile_text:
                for role in ("elite", "strong", "light"):
                    agent = self.agent_chain.get_agent(role)
                    if agent:
                        prefix = f"{profile_text}\n\n---\n\n"
                        if not agent.system_prompt.startswith(prefix):
                            agent.system_prompt = prefix + agent.system_prompt
                logger.info(f"Profile injected into agent prompts")
        except Exception as e:
            logger.warning(f"Failed to inject profile: {e}")

    def set_pipeline(self, pipeline: Pipeline) -> None:
        """注入 Pipeline"""
        self.pipeline = pipeline
        self.evidence = EvidenceChain(pipeline=pipeline)
        self.pipeline.inverse_verifier = self.inverse_verifier

    async def start_run(self, user_input: str, mode: str = "task",
                        on_progress: Optional[Callable] = None,
                        user_confirm_callback: Optional[Callable[[str], bool]] = None,
                        yes: bool = False, dry_run: bool = False) -> dict:
        """
        启动 DAG 流水线完整执行

        参数:
            user_input: 用户需求/BUG 报告
            mode: 执行模式 (task|analysis|auto-approve|manual|cascade|debate)
            on_progress: 进度回调
            user_confirm_callback: 用户确认回调函数（返回 True=确认通过）
            yes: 跳过所有确认
            dry_run: 只输出摘要不执行

        返回:
            包含执行结果 + 证据链的 dict
        """
        logger.info(f"=" * 60)
        logger.info(f"ORCHESTRATOR DAG START: mode={mode}")
        logger.info(f"Input: {user_input[:200]}...")
        logger.info(f"=" * 60)

        # === 意图解析 ===
        parsed = self.intent_parser.parse(user_input)
        resolved_input = parsed.resolved_text or user_input
        suffix = parsed.to_prompt_suffix()
        if suffix:
            resolved_input += "\n\n" + suffix
        logger.info(f"Parsed intent: strategy={parsed.strategy}, files={len(parsed.referenced_files)}")

        # === dry-run 模式 ===
        if dry_run:
            self._show_intent_summary(resolved_input, parsed, execute=False)
            return {
                "id": f"dry_run",
                "mode": "dry_run",
                "input": user_input,
                "parsed": parsed,
                "status": "dry_run",
                "phases": [],
                "result": None,
                "error": None,
                "evidence": self.evidence,
            }

        # === 意图确认 ===
        if not yes and mode not in ("analysis", "cascade", "debate"):
            confirm = self._prompt_user_confirmation(resolved_input, parsed)
            if not confirm:
                logger.info("User cancelled execution")
                # 记录反馈
                self.feedback_learner.record(
                    action_type="reject",
                    key=f"cancel_{mode}",
                    detail=user_input[:200],
                    success=False,
                    context={"mode": mode, "strategy": parsed.strategy},
                )
                return {
                    "id": f"cancelled",
                    "mode": mode,
                    "input": user_input,
                    "status": "cancelled",
                    "phases": [],
                    "result": None,
                    "error": "Cancelled by user",
                    "evidence": self.evidence,
                }

        run = {
            "id": f"run_{__import__('uuid').uuid4().hex[:8]}",
            "mode": mode,
            "input": user_input,
            "status": "initializing",
            "phases": [],
            "result": None,
            "error": None,
            "evidence": self.evidence,
        }

        # 记录命令反馈
        self.feedback_learner.record(
            action_type="command",
            key=f"run_{mode}",
            detail=user_input[:300],
            success=True,
            context={
                "mode": mode,
                "strategy": parsed.strategy,
                "flags": [],
                "file_count": len(parsed.referenced_files),
            },
        )

        if on_progress:
            on_progress(run)

        try:
            if mode == "smart":
                run["phases"] = await self._run_smart_flow(resolved_input)
            elif mode == "analysis":
                run["phases"] = await self._run_analysis_flow(resolved_input)
            elif mode == "cascade":
                run["phases"] = await self._run_cascade_flow(resolved_input)
            elif mode == "debate":
                run["phases"] = await self._run_debate_flow(resolved_input)
            else:
                # 标准完整 DAG 流水线
                run["phases"] = await self._run_full_dag(
                    resolved_input, user_confirm_callback
                )
            run["status"] = "completed"

        except Exception as e:
            logger.error(f"Run failed: {e}")
            run["status"] = "failed"
            run["error"] = str(e)

        run["evidence_report"] = self.evidence.get_report()

        if on_progress:
            on_progress(run)

        return run

    async def _run_full_dag(self, user_input: str,
                            confirm_callback: Optional[Callable] = None) -> list:
        """
        完整 5 阶段 DAG 流水线:
        阶段 0: ConfirmDemand (需求确认)
        阶段 1: RootCause    (根因分析，循环直到 100%)
        阶段 2: Decompose    (任务拆解)
        阶段 3: Execute      (子任务执行，失败重试/升级)
        阶段 4: Review+Patch (全局复核 + 回退)
        """
        phases = []

        # ====== 阶段 0: 需求自我确认 (ConfirmDemand) ======
        logger.info("=" * 40)
        logger.info("PHASE 0: ConfirmDemand - 需求自我确认")
        logger.info("=" * 40)

        confirm_result = await self._phase_confirm_demand(user_input, confirm_callback)
        phases.append(confirm_result)
        self.evidence.add(
            phase="confirm_demand",
            summary=confirm_result.get("summary", "需求确认完成"),
            detail=confirm_result.get("detail", ""),
            evidence=confirm_result.get("evidence", []),
        )

        if not confirm_result.get("confirmed", False):
            logger.warning("User did not confirm demand, aborting")
            return phases

        # 更新上下文
        self.context_manager.add_message("assistant",
            f"[ConfirmDemand] {confirm_result.get('summary', '')[:200]}")

        # ====== 阶段 1: 根因分析 (RootCause) ======
        logger.info("=" * 40)
        logger.info("PHASE 1: RootCause - 根因分析")
        logger.info("=" * 40)

        rootcause_result = await self._phase_root_cause(
            confirm_result.get("confirmed_demand", user_input)
        )
        phases.append(rootcause_result)
        self.evidence.add(
            phase="root_cause",
            summary=rootcause_result.get("summary", "根因分析完成"),
            detail=rootcause_result.get("detail", ""),
            evidence=rootcause_result.get("evidence", []),
        )
        self.context_manager.add_message("assistant",
            f"[RootCause] {rootcause_result.get('summary', '')[:200]}")

        # ====== 阶段 2: 任务拆解 (Decompose) ======
        logger.info("=" * 40)
        logger.info("PHASE 2: Decompose - 任务拆解")
        logger.info("=" * 40)

        decompose_result = await self._phase_decompose(
            rootcause_result.get("root_cause", "")
        )
        phases.append(decompose_result)
        self.evidence.add(
            phase="decompose",
            summary=f"拆解为 {len(decompose_result.get('subtasks', []))} 个子任务",
            detail=json.dumps(decompose_result.get("subtasks", []), ensure_ascii=False),
            evidence=[],
        )

        # ====== 阶段 3: 子任务执行 (Execute) ======
        logger.info("=" * 40)
        logger.info("PHASE 3: Execute - 子任务执行")
        logger.info("=" * 40)

        execute_result = await self._phase_execute(
            decompose_result.get("subtasks", []),
            user_input,
        )
        phases.append(execute_result)
        self.evidence.add(
            phase="execute",
            summary=f"执行 {len(execute_result.get('results', []))} 个子任务",
            detail=json.dumps(execute_result.get("results", []), ensure_ascii=False),
            evidence=execute_result.get("evidence", []),
        )

        # ====== 阶段 4: 全局复核 + Patch (Review) ======
        logger.info("=" * 40)
        logger.info("PHASE 4: Review+Patch - 全局复核")
        logger.info("=" * 40)

        review_result = await self._phase_review_patch(
            execute_result,
            decompose_result.get("subtasks", []),
        )
        phases.append(review_result)
        self.evidence.add(
            phase="review_patch",
            summary=f"复核{'通过' if review_result.get('passed', False) else '不达标'}",
            detail=json.dumps(review_result, ensure_ascii=False),
            evidence=review_result.get("evidence", []),
        )

        # === 如果复核不达标，自动回退重新执行 ===
        max_retry = 2
        retry_count = 0
        while not review_result.get("passed", False) and retry_count < max_retry:
            retry_count += 1
            logger.warning(f"Review not passed, retrying execute (attempt {retry_count}/{max_retry})...")

            # 重新执行阶段 3
            execute_result = await self._phase_execute(
                decompose_result.get("subtasks", []),
                user_input,
                retry_context=review_result.get("review_feedback", ""),
            )
            phases.append({"name": f"execute_retry_{retry_count}", **execute_result})

            # 重新复核
            review_result = await self._phase_review_patch(
                execute_result,
                decompose_result.get("subtasks", []),
            )
            phases.append({"name": f"review_retry_{retry_count}", **review_result})

        return phases

    # ====== 阶段实现 ======

    async def _phase_confirm_demand(self, user_input: str,
                                    confirm_callback: Optional[Callable] = None) -> dict:
        """
        阶段 0: 需求自我确认

        流程:
        1. Strong/Elite 自问自答理解需求
        2. 生成需求摘要
        3. 请求用户确认（如果提供了回调）
        4. 循环直到确认通过
        """
        agent = self.agent_chain.get_agent("elite") or self.agent_chain.get_agent("strong")

        demand_summary = ""
        if agent:
            # 自问自答理解需求
            prompt = (
                f"You are analyzing a user request. Follow this self-questioning process:\n\n"
                f"1. What is the core problem or requirement?\n"
                f"2. What are the acceptance criteria?\n"
                f"3. What assumptions am I making?\n"
                f"4. What information is missing?\n\n"
                f"User request: {user_input}\n\n"
                f"Generate a concise demand summary (Chinese) covering: core requirement, acceptance criteria, scope."
            )
            demand_summary = await agent.execute(prompt=prompt)
        else:
            demand_summary = user_input

        # 构建确认数据
        result = {
            "name": "confirm_demand",
            "status": "completed",
            "summary": demand_summary[:500],
            "detail": demand_summary,
            "confirmed_demand": demand_summary,
            "evidence": [{"type": "demand_summary", "content": demand_summary}],
            "confirmed": True,  # 默认确认
        }

        # 如果有用户确认回调，等待确认
        if confirm_callback:
            confirmed = confirm_callback(demand_summary)
            result["confirmed"] = confirmed
            logger.info(f"User confirmation: {'PASSED' if confirmed else 'REJECTED'}")

        return result

    async def _phase_root_cause(self, demand: str) -> dict:
        """
        阶段 1: 根因分析（证据驱动）

        使用 EvidenceBasedRootCausePipeline（如果已配置）进行源码+日志双驱动分析：
        1. load_evidences —— 加载源码上下文 + 运行时日志
        2. free_analysis —— 强模型基于证据自由分析
        3. uncertainty_monitor —— 反幻觉双重扫描
        4. structured_debate —— 辩论子图（Challenger + Judge）
        5. final_output —— 结论 + 证据索引

        fallback 到原有 Elite Agent 自问自答。
        """
        # 如果配置了 EvidenceBasedRootCausePipeline，优先使用
        if self.root_cause_pipeline is not None:
            logger.info("Using EvidenceBasedRootCausePipeline for root cause analysis")
            try:
                rc_state = await self.root_cause_pipeline.run(
                    task_description=demand,
                )
                return {
                    "name": "root_cause",
                    "status": "completed",
                    "summary": (rc_state.final_conclusion or rc_state.free_analysis_result or "")[:500],
                    "detail": rc_state.final_conclusion or rc_state.free_analysis_result or "",
                    "root_cause": rc_state.final_conclusion or rc_state.free_analysis_result or "",
                    "confidence": rc_state.debate_evidence_score or 50.0,
                    "evidence": [
                        {"type": "pipeline_state", "content": rc_state.to_dict()},
                    ],
                    "debate_passed": rc_state.debate_passed,
                    "uncertainty_detected": rc_state.uncertainty_detected,
                }
            except Exception as e:
                logger.warning(f"EvidenceBasedRootCausePipeline failed, falling back: {e}")

        # fallback: Elite Agent 自问自答
        agent = self.agent_chain.get_agent("elite")
        if not agent:
            return {
                "name": "root_cause",
                "status": "failed",
                "summary": "No elite agent available",
                "root_cause": demand,
                "evidence": [],
            }

        max_iterations = 3
        root_cause = ""
        confidence = 0.0

        for i in range(max_iterations):
            prompt = (
                f"Root cause analysis - iteration {i + 1}/{max_iterations}\n\n"
                f"Demand: {demand}\n\n"
                f"Follow this systematic approach:\n"
                f"1. What is the symptom?\n"
                f"2. What is the direct cause?\n"
                f"3. What is the root cause?\n"
                f"4. What evidence supports this? (logs, code, tests)\n"
                f"5. Are there alternative explanations?\n"
                f"6. Confidence level (0-100%):\n\n"
                f"Output in structured format:\n"
                f"ROOT_CAUSE: <one sentence>\n"
                f"EVIDENCE: <evidence details>\n"
                f"CONFIDENCE: <percentage>\n"
                f"ALTERNATIVES: <if any>"
            )
            result = await agent.execute(prompt=prompt)

            try:
                for line in result.split("\n"):
                    if "CONFIDENCE" in line:
                        num = "".join(c for c in line if c.isdigit() or c == ".")
                        if num:
                            confidence = float(num.rstrip(".%"))
            except Exception:
                pass

            root_cause = result

            if confidence >= 90.0:
                logger.info(f"Root cause confirmed at iteration {i + 1} with {confidence}% confidence")
                break

            logger.info(f"Root cause iteration {i + 1}: {confidence}% confidence, continuing...")

        return {
            "name": "root_cause",
            "status": "completed",
            "summary": root_cause[:500],
            "detail": root_cause,
            "root_cause": root_cause,
            "confidence": confidence,
            "iterations": max_iterations,
            "evidence": [{"type": "root_cause_analysis", "content": root_cause}],
        }

    async def _phase_decompose(self, root_cause: str) -> dict:
        """
        阶段 2: 任务拆解

        流程:
        1. Elite/Strong 将大任务拆解为颗粒化子任务
        2. 子任务唯一 ID + 依赖关系
        3. 写入 SQLite 检查点
        """
        agent = self.agent_chain.get_agent("elite") or self.agent_chain.get_agent("strong")

        subtasks = []
        if agent:
            prompt = (
                f"Decompose the following root cause / task into granular subtasks.\n\n"
                f"Root cause: {root_cause[:1000]}\n\n"
                f"Rules:\n"
                f"- Each subtask must have a unique ID (S001, S002, ...)\n"
                f"- Each subtask must have a clear, single responsibility\n"
                f"- Specify dependencies between subtasks\n"
                f"- Assign each to the right agent type: elite, strong, or light\n"
                f"- Subtasks must be granular (one file change, one test, etc.)\n\n"
                f"Output as JSON list:\n"
                f'[{{"step_id": "S001", "description": "...", "assigned_agent": "elite|strong|light", "dependencies": []}}]'
            )
            result = await agent.execute(prompt=prompt)

            # 尝试解析 JSON
            try:
                # 提取 JSON 数组
                import re
                json_match = re.search(r'\[.*?\]', result, re.DOTALL)
                if json_match:
                    subtasks = json.loads(json_match.group())
            except (json.JSONDecodeError, Exception):
                # fallback: 手动解析
                pass

        # 如果没有解析出子任务，生成一个默认任务
        if not subtasks:
            subtasks = [{
                "step_id": "S001",
                "description": f"Execute: {root_cause[:200]}",
                "assigned_agent": "light",
                "dependencies": [],
            }]

        # 持久化到 SQLite
        if self.pipeline:
            task = Task(
                task_id=f"decompose_{__import__('uuid').uuid4().hex[:8]}",
                description=root_cause[:200],
                type="generic",
            )
            for st in subtasks:
                task.steps.append(TaskStep(
                    step_id=st["step_id"],
                    description=st["description"],
                    assigned_agent=st["assigned_agent"],
                ))
            self.pipeline.add_task(task)

        return {
            "name": "decompose",
            "status": "completed",
            "summary": f"Decomposed into {len(subtasks)} subtasks",
            "subtasks": subtasks,
            "evidence": [{"type": "task_decomposition", "content": json.dumps(subtasks, ensure_ascii=False)}],
        }

    @staticmethod
    def _find_target_files(user_input: str) -> list[str]:
        """从 user_input 中找到需要修改的 .py 文件"""
        import re, glob as _glob
        files = []
        for m in re.finditer(r'([\w./-]+\.py)', user_input):
            c = os.path.join('src', m.group(1))
            if os.path.exists(c): files.append(c); continue
            if os.path.exists(m.group(1)): files.append(m.group(1))
        if not files: files.extend(_glob.glob("src/**/*.py", recursive=True))
        if not files: files.extend(p for p in _glob.glob("*.py") if p not in ("main.py","rpc_server.py","batch_runner.py","run_engine.py"))
        return files[:3]

    @staticmethod
    def _apply_patch(result_text: str, default_file: str = "") -> list[dict]:
        """解析模型输出中的代码块并写入磁盘"""
        import re
        applied = []
        for fpath, code in re.findall(
            r'FILEPATH:\s*(.+?)[\r\n]+```(?:\w+)?\n(.+?)\n```',
            result_text, re.DOTALL
        ):
            fpath = fpath.strip().strip('"\'')
            try:
                os.makedirs(os.path.dirname(fpath) or ".", exist_ok=True)
                with open(fpath, "w", encoding="utf-8") as f:
                    f.write(code.strip() + "\n")
                applied.append({"file": fpath, "bytes": len(code)})
            except Exception as e:
                logger.warning(f"Failed to write {fpath}: {e}")
        if applied: return applied
        blocks = re.findall(r'```(?:\w+)?\n(.+?)\n```', result_text, re.DOTALL)
        if blocks and default_file:
            try:
                with open(default_file, "w", encoding="utf-8") as f:
                    f.write(blocks[0].strip() + "\n")
                applied.append({"file": default_file, "bytes": len(blocks[0])})
            except Exception as e:
                logger.warning(f"Failed to write {default_file}: {e}")
        return applied

    async def _phase_execute(self, subtasks: list, user_input: str,
                              retry_context: str = "") -> dict:
        """
        阶段 3: 子任务执行

        流程:
        1. 从 user_input 推断目标文件
        2. 读取文件当前内容传给模型
        3. 模型输出修改后的完整文件
        4. 解析代码块并写回磁盘
        """
        results = []
        evidence = []
        all_passed = True

        # 找到需要修改的目标文件
        target_files = self._find_target_files(user_input)
        if not target_files:
            target_files = ["src/parser.py", "src/auth.py", "src/db.py"]

        for st in subtasks:
            step_id = st["step_id"] if isinstance(st, dict) else st
            description = st["description"] if isinstance(st, dict) else st
            assigned = st.get("assigned_agent", "light") if isinstance(st, dict) else "light"

            agent = self.agent_chain.get_agent(assigned)
            if not agent:
                agent = self.agent_chain.get_agent("light")

            if not agent:
                results.append({
                    "step_id": step_id,
                    "status": "failed",
                    "error": "No suitable agent",
                })
                all_passed = False
                continue

            # 读取目标文件当前源码
            file_context = ""
            default_file = ""
            for f in target_files:
                full = os.path.join(os.getcwd(), f)
                if os.path.exists(full):
                    with open(full, "r", encoding="utf-8") as fh:
                        content = fh.read()
                    file_context += f"\n--- {f} (current) ---\n{content}"
                    if not default_file:
                        default_file = f
                elif os.path.exists(f):
                    with open(f, "r", encoding="utf-8") as fh:
                        content = fh.read()
                    file_context += f"\n--- {f} (current) ---\n{content}"
                    if not default_file:
                        default_file = f

            max_attempts = 3
            step_result = None
            step_passed = False
            step_patches = []

            for attempt in range(max_attempts):
                prompt = (
                    f"You are a code editor. Fix the issue described below.\n\n"
                    f"Issue: {user_input[:800]}\n"
                    f"Subtask: {description}\n"
                )
                if retry_context:
                    prompt += f"\nFeedback from reviewer: {retry_context}\n"

                prompt += (
                    f"\nCurrent file content(s):"
                    f"{file_context[:2000]}\n\n"
                    f"---\n"
                    f"IMPORTANT: Output ONLY the modified file. Use this exact format:\n"
                    f"FILEPATH: <filename>\n"
                    f"```\n"
                    f"<entire file content, with your fix applied>\n"
                    f"```\n"
                    f"Do NOT add explanations before or after. Output code only."
                )

                try:
                    step_result = await agent.execute(prompt=prompt)
                    step_patches = self._apply_patch(step_result, default_file)
                    step_passed = True
                    logger.info(f"Subtask {step_id} completed on attempt {attempt + 1}, {len(step_patches)} file(s) written")
                    break
                except Exception as e:
                    logger.warning(f"Subtask {step_id} attempt {attempt + 1} failed: {e}")
                    if attempt < max_attempts - 1:
                        await asyncio.sleep(2 ** attempt)

            results.append({
                "step_id": step_id,
                "status": "done" if step_passed else "failed",
                "result": step_result,
                "patches": step_patches,
                "error": None if step_passed else f"Failed after {max_attempts} attempts",
                "assigned_agent": assigned,
            })
            evidence.append({
                "type": "subtask_result",
                "step_id": step_id,
                "status": "done" if step_passed else "failed",
                "content": (step_result or "")[:200],
                "patches": step_patches,
            })

            if not step_passed:
                all_passed = False

            if self.pipeline:
                self.pipeline.save_checkpoint(
                    task_id=f"execute_{step_id}",
                    step_id=step_id,
                    phase="execute",
                    status="done" if step_passed else "failed",
                    output=(step_result or "")[:500],
                )

        return {
            "name": "execute",
            "status": "completed",
            "summary": f"Executed {len(subtasks)} subtasks, all_passed={all_passed}",
            "results": results,
            "all_passed": all_passed,
            "evidence": evidence,
        }

    async def _phase_review_patch(self, execute_result: dict,
                                   subtasks: list) -> dict:
        """
        阶段 4: 全局复核 + Patch

        流程:
        1. Strong Agent 校验 diff & 测试
        2. 生成最终补丁 & 质量评分
        3. 不达标自动回退重做
        """
        reviewer = self.agent_chain.get_agent("strong")
        if not reviewer:
            return {
                "name": "review_patch",
                "status": "skipped",
                "passed": True,
                "summary": "No reviewer, auto-pass",
                "quality_score": 100,
                "evidence": [],
            }

        # 收集所有执行结果
        results_text = ""
        for r in execute_result.get("results", []):
            results_text += f"\n--- Step {r['step_id']} ({r['status']}) ---\n"
            if r.get("result"):
                results_text += r["result"][:500] + "\n"
            if r.get("error"):
                results_text += f"ERROR: {r['error']}\n"

        review_prompt = (
            f"Review the following task execution results:\n\n"
            f"Subtasks to review: {len(subtasks)}\n"
            f"Execute results: {results_text[:2000]}\n\n"
            f"Evaluate:\n"
            f"1. Are all subtasks completed correctly?\n"
            f"2. Is the solution complete and consistent?\n"
            f"3. Quality score (0-100):\n"
            f"4. Specific issues or feedback:\n"
            f"5. Final verdict: PASS or FAIL\n\n"
            f"Output format:\n"
            f"QUALITY_SCORE: <0-100>\n"
            f"VERDICT: <PASS|FAIL>\n"
            f"FEEDBACK: <detailed feedback>\n"
            f"PATCH: <if applicable>"
        )

        review_result = await reviewer.execute(prompt=review_prompt)

        # 解析结果
        passed = False
        quality_score = 0
        feedback = ""
        patch = ""

        for line in review_result.split("\n"):
            if "VERDICT" in line and "PASS" in line.upper():
                passed = True
            elif "QUALITY_SCORE" in line:
                num = "".join(c for c in line if c.isdigit() or c == ".")
                if num:
                    quality_score = float(num.rstrip(".%"))
                    if quality_score >= 60:
                        passed = True
            elif "FEEDBACK" in line:
                feedback = line.split(":", 1)[-1].strip()
            elif "PATCH" in line:
                patch = line.split(":", 1)[-1].strip()

        logger.info(f"Review verdict: {'PASS' if passed else 'FAIL'} (score={quality_score})")

        return {
            "name": "review_patch",
            "status": "completed" if passed else "failed",
            "passed": passed,
            "quality_score": quality_score,
            "feedback": feedback or review_result[:200],
            "patch": patch,
            "summary": review_result[:500],
            "detail": review_result,
            "evidence": [{
                "type": "review_result",
                "content": review_result,
                "passed": passed,
                "score": quality_score,
            }],
        }

    # ====== 意图确认面板 ======

    def _show_intent_summary(self, resolved_input: str, parsed: ParsedIntent,
                              execute: bool = True) -> None:
        """显示意图摘要面板（dry-run 或真正执行前）"""
        if _console:
            panel = Panel.fit(
                f"[bold]原始输入:[/bold]\n{parsed.raw_text[:200]}\n\n"
                f"[bold]解析后:[/bold]\n{resolved_input[:300]}\n\n"
                f"[bold]策略:[/bold] {parsed.strategy}\n"
                f"[bold]引用文件:[/bold] {len(parsed.referenced_files)} 个\n"
                f"[bold]引用任务:[/bold] {len(parsed.referenced_tasks)} 个\n"
                f"[bold]执行:[/bold] {'将执行' if execute else 'dry-run, 不执行'}",
                title="意图摘要",
                border_style="cyan",
            )
            _console.print(panel)
        else:
            logger.info(f"Intent summary: {parsed.raw_text[:100]} (execute={execute})")

    def _prompt_user_confirmation(self, resolved_input: str, parsed: ParsedIntent) -> bool:
        """
        交互式确认：显示 intent 摘要给用户，等待 y/n 输入

        返回 True=确认通过, False=用户取消
        """
        if _console is None:
            logger.info("No rich console available, auto-confirming")
            return True

        # 显示意图摘要
        table = Table(title="意图确认", box=None)
        table.add_column("项目", style="cyan", no_wrap=True)
        table.add_column("内容")
        table.add_row("原始输入", parsed.raw_text[:150])
        table.add_row("策略", parsed.strategy)
        table.add_row("引用文件", str(len(parsed.referenced_files)))
        table.add_row("引用任务", str(len(parsed.referenced_tasks)))
        _console.print(table)

        # 显示文件详情
        if parsed.referenced_files:
            _console.print(f"\n[bold]引用文件详情:[/bold]")
            for f in parsed.referenced_files[:5]:
                _console.print(f"  [file]{f['path']}[/file] ({len(f['content'])}B)")

        _console.print("\n[bold yellow]执行此意图？[/bold yellow]")

        try:
            import sys
            # 尝试从 stdin 读取确认
            if hasattr(sys, "stdin") and sys.stdin.isatty():
                answer = input("输入 y 确认 / n 取消 / s 跳过确认: ").strip().lower()
                if answer in ("y", "yes", ""):
                    _console.print("[green]已确认，继续执行[/green]")
                    return True
                elif answer in ("s", "skip"):
                    _console.print("[yellow]跳过确认，继续执行[/yellow]")
                    self._skip_confirmation = True
                    return True
                else:
                    _console.print("[red]已取消[/red]")
                    return False
            else:
                # 非交互式终端，默认确认
                _console.print("[yellow]非交互终端，自动确认[/yellow]")
                return True
        except (EOFError, KeyboardInterrupt):
            _console.print("[red]输入中断，视为取消[/red]")
            return False

    # ====== 快捷流水线 ======

    async def _run_analysis_flow(self, user_input: str) -> list:
        """仅分析模式：ConfirmDemand + RootCause"""
        phases = []

        confirm = await self._phase_confirm_demand(user_input)
        phases.append(confirm)

        rc = await self._phase_root_cause(
            confirm.get("confirmed_demand", user_input)
        )
        phases.append(rc)

        return phases

    async def _run_cascade_flow(self, user_input: str) -> list:
        """级联模式：全流程一次过"""
        return await self._run_full_dag(user_input)

    async def _run_debate_flow(self, user_input: str) -> list:
        """辩论模式"""
        agent = self.agent_chain.get_agent("elite")
        if agent:
            result = await agent.execute(
                prompt=f"Debate mode analysis:\nInput: {user_input}"
            )
            return [{"name": "debate", "result": result}]
        return [{"name": "debate", "result": user_input}]

    # ====== 模式 B: 智能调度 ======

    async def _run_smart_flow(self, user_input: str) -> list:
        """
        智能调度模式 - 委托 IntelligentOrchestrator 执行

        IntelligentOrchestrator 使用 LangGraph StateGraph 实现:
        1. task_analyzer —— 复杂度评估 → 自动选择本地/云端模型
        2. resource_allocator —— 按可用性+成本分配 Planner+Workers
        3. plan_decomposer —— 拆解为原子性子任务
        4. parallel_executor —— asyncio.gather 多 Worker 并发，失败降级
        5. review_and_debate —— 实事求是审查 + 辩论闭环
        6. checkpoint_saver —— SQLite 检查点持久化
        7. final_synthesizer —— Markdown 报告输出
        """
        logger.info("=" * 40)
        logger.info("SMART MODE: IntelligentOrchestrator")
        logger.info("=" * 40)

        phases = []

        try:
            result_state = await self.intelligent_orchestrator.run(
                task=user_input,
            )

            phases.append({
                "name": "smart_orchestration",
                "status": result_state.status,
                "complexity": result_state.task_complexity,
                "assigned_models": result_state.assigned_models,
                "sub_tasks": [s.to_dict() for s in result_state.sub_tasks],
                "parallel_failures": result_state.parallel_failures,
                "review_passed": result_state.review_passed,
                "review_score": result_state.review_score,
                "review_feedback": result_state.review_feedback,
                "global_result": result_state.global_result,
                "loop_count": result_state.loop_count,
            })

            self.evidence.add(
                phase="smart_orchestration",
                summary=f"智能调度完成: complexity={result_state.task_complexity}, "
                        f"review={'PASS' if result_state.review_passed else 'REJECT'}, "
                        f"subtasks={len(result_state.sub_tasks)}",
                detail=result_state.global_result or "",
                evidence=[{"type": "orchestrator_state", "content": result_state.to_dict()}],
            )

        except Exception as e:
            logger.error(f"Smart orchestration failed: {e}")
            phases.append({
                "name": "smart_orchestration",
                "status": "failed",
                "error": str(e),
            })

        return phases