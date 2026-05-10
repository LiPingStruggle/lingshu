#!/usr/bin/env python3
"""
AgentModes - 9 种 Agent 链协作模式

来源（第 5 章 REQUIREMENTS.md）:
1. sequential      - 串行链，逐步传递
2. parallel        - 并行执行 + 投票/择优/合并
3. debate          - 正反方多轮辩论 + 裁判裁决
4. cascade         - 强模型拆→弱模型执行→复核
5. iterative       - 循环执行直到质量达标
6. architect-editor - 强模型设计→弱模型实现→复核
7. agent-loop      - 推理→工具→观察自主循环
8. plan-agent-yolo - 规划→执行→审批三级链
9. best-of-n       - N 方案并行择优
"""
from __future__ import annotations
import asyncio
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class AgentMode:
    """单个 Agent 协作模式配置"""
    id: str
    name: str
    description: str
    handler: Callable[..., Awaitable[dict]]
    tags: list[str] = field(default_factory=list)
    max_rounds: int = 3


class AgentModeRegistry:
    """模式注册中心"""

    def __init__(self):
        self._modes: dict[str, AgentMode] = {}
        self._register_default_modes()

    def _register_default_modes(self):
        from core.agent_chain import AgentChain

        self.register(AgentMode(
            id="sequential",
            name="串行链",
            description="串行链，逐步传递执行",
            handler=lambda chain, task, **kw: self._run_sequential(chain, task, **kw),
            tags=["default", "simple"],
        ))
        self.register(AgentMode(
            id="parallel",
            name="并行执行",
            description="并行执行 + 投票/择优/合并",
            handler=lambda chain, task, **kw: self._run_parallel(chain, task, **kw),
            tags=["multi-agent"],
        ))
        self.register(AgentMode(
            id="debate",
            name="辩论模式",
            description="正反方多轮辩论 + 裁判裁决",
            handler=lambda chain, task, **kw: self._run_debate(chain, task, **kw),
            tags=["decision", "review"],
        ))
        self.register(AgentMode(
            id="cascade",
            name="级联模式",
            description="强模型拆→弱模型执行→复核",
            handler=lambda chain, task, **kw: self._run_cascade(chain, task, **kw),
            tags=["complex", "efficient"],
        ))
        self.register(AgentMode(
            id="iterative",
            name="迭代优化",
            description="循环执行直到质量达标",
            handler=lambda chain, task, **kw: self._run_iterative(chain, task, **kw),
            tags=["quality", "refinement"],
        ))
        self.register(AgentMode(
            id="architect-editor",
            name="架构师-编辑模式",
            description="强模型设计→弱模型实现→复核",
            handler=lambda chain, task, **kw: self._run_architect_editor(chain, task, **kw),
            tags=["codegen", "aider"],
        ))
        self.register(AgentMode(
            id="agent-loop",
            name="自主循环",
            description="推理→工具→观察自主循环",
            handler=lambda chain, task, **kw: self._run_agent_loop(chain, task, **kw),
            tags=["autonomous", "agentic"],
        ))
        self.register(AgentMode(
            id="plan-agent-yolo",
            name="三级审批链",
            description="规划→执行→审批三级链",
            handler=lambda chain, task, **kw: self._run_plan_agent_yolo(chain, task, **kw),
            tags=["safe", "controlled"],
        ))
        self.register(AgentMode(
            id="best-of-n",
            name="N 方案择优",
            description="N 方案并行择优",
            handler=lambda chain, task, **kw: self._run_best_of_n(chain, task, **kw),
            tags=["quality", "selection"],
        ))

    def register(self, mode: AgentMode):
        self._modes[mode.id] = mode
        logger.info(f"Registered agent mode: {mode.id}")

    def get(self, mode_id: str) -> Optional[AgentMode]:
        return self._modes.get(mode_id)

    def list_modes(self) -> list[dict]:
        return [{
            "id": m.id,
            "name": m.name,
            "description": m.description,
            "tags": m.tags,
        } for m in self._modes.values()]

    async def execute(self, mode_id: str, chain, task, **kwargs) -> dict:
        mode = self.get(mode_id)
        if not mode:
            raise ValueError(f"Unknown mode: {mode_id}")
        logger.info(f"Executing mode: {mode_id}")
        return await mode.handler(chain, task, **kwargs)

    # ========== 模式实现 ==========

    async def _run_sequential(self, chain, task, **kw) -> dict:
        """串行链：按顺序执行所有步骤"""
        from workflows.task_workflow import TaskStep
        results = []
        for step in task.steps:
            result = await chain.dispatch_step(step, task)
            results.append({"step_id": step.step_id, "result": result.result, "status": result.status})
            if result.status == "failed":
                return {"mode": "sequential", "status": "failed", "results": results, "failed_step": step.step_id}
        return {"mode": "sequential", "status": "done", "results": results}

    async def _run_parallel(self, chain, task, **kw) -> dict:
        """并行执行：同时 dispatch 所有步骤"""
        async def run_step(step):
            result = await chain.dispatch_step(step, task)
            return {"step_id": step.step_id, "result": result.result, "status": result.status}
        sem = asyncio.Semaphore(kw.get("max_concurrency", 5))
        async def limited(step):
            async with sem:
                return await run_step(step)
        tasks = [limited(s) for s in task.steps]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        final = []
        for r in results:
            if isinstance(r, Exception):
                final.append({"status": "failed", "error": str(r)})
            else:
                final.append(r)
        all_ok = all(r.get("status") == "done" for r in final if isinstance(r, dict))
        return {"mode": "parallel", "status": "done" if all_ok else "partial", "results": final}

    async def _run_debate(self, chain, task, **kw) -> dict:
        """辩论模式：正方→反方→裁判"""
        elite = chain.get_agent("elite")
        strong = chain.get_agent("strong")
        if not elite or not strong:
            return {"mode": "debate", "status": "failed", "error": "Need elite + strong agents"}

        rounds = kw.get("rounds", 3)
        proposition = await elite.execute(prompt=f"提出一个方案解决: {task.description}")
        for r in range(rounds):
            opposition = await elite.execute(prompt=f"反驳以下方案，指出漏洞:\n{proposition}")
            proposition = await strong.execute(prompt=f"回应以下反驳，完善方案:\n方案: {proposition}\n反驳: {opposition}")
        verdict = await strong.execute(prompt=f"作为裁判，裁决以下辩论的最终结论:\n{proposition}")
        return {"mode": "debate", "status": "done", "proposition": proposition, "verdict": verdict}

    async def _run_cascade(self, chain, task, **kw) -> dict:
        """级联模式：Strong 拆解 → Light 执行 → Strong 复核"""
        strong = chain.get_agent("strong")
        light = chain.get_agent("light")
        if not strong or not light:
            return {"mode": "cascade", "status": "failed", "error": "Need strong + light agents"}
        plan = await strong.execute(prompt=f"拆解以下任务为子步骤:\n{task.description}")
        execution = await light.execute(prompt=f"执行以下计划:\n{plan}")
        review = await strong.execute(prompt=f"复核以下执行结果:\n{execution}")
        passed = "pass" in review.lower()
        return {"mode": "cascade", "status": "done" if passed else "failed", "plan": plan, "execution": execution, "review": review}

    async def _run_iterative(self, chain, task, **kw) -> dict:
        """迭代优化：循环直到质量达标"""
        max_iter = kw.get("max_iterations", 5)
        quality_threshold = kw.get("quality_threshold", 80)
        light = chain.get_agent("light")
        strong = chain.get_agent("strong")
        result = ""
        for i in range(max_iter):
            result = await light.execute(prompt=f"迭代 {i+1}: {task.description}\n前次结果: {result[:500] if result else '无'}")
            review = await strong.execute(prompt=f"评分(0-100)并反馈:\n{result}")
            # Parse score
            score = 0
            for line in review.split("\n"):
                for tok in line.split():
                    try:
                        score = int(tok)
                    except ValueError:
                        continue
            if score >= quality_threshold:
                return {"mode": "iterative", "status": "done", "iterations": i+1, "final_score": score, "result": result}
        return {"mode": "iterative", "status": "partial", "iterations": max_iter, "result": result}

    async def _run_architect_editor(self, chain, task, **kw) -> dict:
        """架构师-编辑：Strong 设计 → Light 实现 → Strong 复核"""
        strong = chain.get_agent("strong")
        light = chain.get_agent("light")
        design = await strong.execute(prompt=f"作为架构师，设计解决方案:\n{task.description}")
        implementation = await light.execute(prompt=f"作为实现者，按设计编码:\n设计:\n{design}")
        review = await strong.execute(prompt=f"作为技术负责人，复核实现:\n{implementation}")
        passed = "pass" in review.lower()
        return {"mode": "architect-editor", "status": "done" if passed else "failed", "design": design, "implementation": implementation, "review": review}

    async def _run_agent_loop(self, chain, task, **kw) -> dict:
        """自主循环：推理→行动→观察"""
        max_steps = kw.get("max_steps", 10)
        light = chain.get_agent("light")
        observation = f"Task: {task.description}"
        history = []
        for step in range(max_steps):
            action = await light.execute(prompt=f"步骤 {step+1}. 观察: {observation}\n输出下一个行动(Action):")
            result = f"[Executed action {step+1}]"
            observation = await light.execute(prompt=f"行动: {action}\n结果: {result}\n输出新的观察(Observation):")
            history.append({"step": step+1, "action": action, "observation": observation})
            if "完成" in observation or "done" in observation.lower():
                return {"mode": "agent-loop", "status": "done", "steps": step+1, "history": history}
        return {"mode": "agent-loop", "status": "partial", "steps": max_steps, "history": history}

    async def _run_plan_agent_yolo(self, chain, task, **kw) -> dict:
        """三级审批：Planner→Agent→YOLO(审批)"""
        strong = chain.get_agent("strong")
        light = chain.get_agent("light")
        plan = await strong.execute(prompt=f"制定详细执行计划:\n{task.description}")
        execution = await light.execute(prompt=f"按计划执行，不可偏离:\n{plan}")
        approval = await strong.execute(prompt=f"审批以下执行结果，输出 APPROVED/REJECTED:\n{execution}")
        approved = "approved" in approval.lower()
        return {"mode": "plan-agent-yolo", "status": "approved" if approved else "rejected", "plan": plan, "execution": execution, "approval": approval}

    async def _run_best_of_n(self, chain, task, **kw) -> dict:
        """N 方案择优：并行生成 N 个方案，选最优"""
        n = kw.get("n", 3)
        light = chain.get_agent("light")
        strong = chain.get_agent("strong")

        async def generate(i):
            return await light.execute(prompt=f"方案 {i+1}/{n}: {task.description}")

        candidates = await asyncio.gather(*[generate(i) for i in range(n)])
        selection_prompt = "从以下方案中选择最佳，说明理由:\n"
        for i, c in enumerate(candidates):
            selection_prompt += f"\n--- 方案 {i+1} ---\n{c[:500]}\n"
        best = await strong.execute(prompt=selection_prompt)
        return {"mode": "best-of-n", "status": "done", "candidates": candidates, "selection": best}