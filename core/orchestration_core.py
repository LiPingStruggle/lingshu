#!/usr/bin/env python3
# 灵枢 (LingShu) - 多模型多Agent智能调度总控，道法自然，任务不辍
"""
IntelligentOrchestrator - 智能调度总控核心

《灵枢》的大脑，将《灵枢》从一个“工具”进化为真正有“调度智慧”的AI工程指挥部。

核心能力:
  1. 智能模型调度：根据复杂度、可用性、成本自动选择本地模型(Ollama)或云端大模型
  2. 多Agent并发执行：并行分配子任务，多个模型/Agent同时工作
  3. 100%任务完成保障：自动降级、重试、检查点、断点恢复
  4. 证据驱动与实事求是审查：源码+日志双驱动，零猜测，最高审查标准
  5. 全量可配置日志：所有决策可追溯

架构:
  task_analyzer → resource_allocator → plan_decomposer
      ↓
  parallel_executor (asyncio.gather 多Agent并发)
      ↓
  review_and_debate (实事求是审查，失败则循环)
      ↓
  checkpoint_saver (每阶段持久化)
      ↓
  final_synthesizer → 完成

设计哲学:
  "知不知，尚矣；不知知，病也。" ——《道德经》
  本地模型(轻骑兵)处理高频低延迟；云端大模型(重装军)攻坚复杂推理。
  调度大脑动态自动不中断，实现100%任务完成。
"""
from __future__ import annotations
import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime
from typing import Optional, Callable, Any

logger = logging.getLogger("lingshu.orchestrator")

# ====== LangGraph 导入 ======
try:
    from langgraph.graph import StateGraph, END
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph.state import CompiledStateGraph
    HAS_LANGGRAPH = True
except ImportError:
    HAS_LANGGRAPH = False
    StateGraph = object
    END = "__end__"
    MemorySaver = object
    CompiledStateGraph = object

# ====== 日志配置 ======


def configure_orchestrator_logger(
    level: str = "DEBUG",
    log_file: Optional[str] = None,
) -> logging.Logger:
    """配置智能调度总控专用日志器"""
    fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    root_logger = logging.getLogger("lingshu.orchestrator")
    root_logger.setLevel(getattr(logging, level.upper(), logging.DEBUG))
    root_logger.handlers.clear()

    stderr = logging.StreamHandler()
    stderr.setFormatter(logging.Formatter(fmt))
    root_logger.addHandler(stderr)

    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter(fmt))
        root_logger.addHandler(fh)

    return root_logger


# ====== 配置数据结构 ======


@dataclass
class ModelPoolConfig:
    """模型池配置 —— 定义所有可用模型及降级链"""
    # 云端模型（按能力降序）
    elite_models: list[str] = field(default_factory=lambda: ["claude-sonnet-4", "gpt-4.1"])
    strong_models: list[str] = field(default_factory=lambda: ["gpt-4.1", "deepseek-chat"])
    medium_models: list[str] = field(default_factory=lambda: ["gpt-4o-mini"])
    # 本地模型（通过 Ollama）
    local_models: list[str] = field(default_factory=lambda: ["qwen2", "qwen2.5-coder"])
    # 降级链（从左到右降级）
    fallback_chain: list[str] = field(default_factory=lambda: ["local", "light", "medium", "strong", "elite"])
    # 并发限制
    max_concurrent_workers: int = 5
    max_retries_per_task: int = 3


@dataclass
class OrchestratorConfig:
    """智能调度总控配置"""
    model_pool: ModelPoolConfig = field(default_factory=ModelPoolConfig)
    analyzer_model: str = "auto"  # auto=自动选择
    checkpoint_db: str = ".lingshu/orchestrator_checkpoints.db"
    max_loops: int = 3
    watchout_timeout_minutes: int = 30


# ====== 状态定义 ======


@dataclass
class SubTask:
    """单个子任务"""
    id: str
    description: str
    status: str = "pending"  # pending | running | done | failed
    assigned_model: str = ""
    result: str = ""
    error: str = ""
    evidence_codes: list[str] = field(default_factory=list)
    evidence_logs: list[str] = field(default_factory=list)
    retry_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class OrchestratorState:
    """IntelligentOrchestrator 的完整状态"""
    # 任务
    task: str
    task_complexity: Optional[str] = None           # simple | moderate | complex
    assigned_models: dict = field(default_factory=dict)  # {role: model_name}
    execution_plan: Optional[dict] = None

    # 子任务并发执行
    sub_tasks: list[SubTask] = field(default_factory=list)
    parallel_failures: int = 0

    # 证据
    code_context: str = ""
    runtime_log: str = ""
    code_file_path: str = ""
    log_file_path: str = ""

    # 审查
    review_passed: Optional[bool] = None
    review_score: Optional[float] = None
    review_feedback: str = ""

    # 全局
    global_result: Optional[str] = None
    error_count: int = 0
    checkpoint_data: Optional[dict] = None
    loop_count: int = 0
    max_loops: int = 3

    # 最终元数据
    errors: list[str] = field(default_factory=list)
    status: str = "pending"
    started_at: str = ""
    completed_at: str = ""

    def to_dict(self) -> dict:
        return {
            "task": self.task[:200],
            "task_complexity": self.task_complexity,
            "assigned_models": self.assigned_models,
            "sub_tasks": [s.to_dict() for s in self.sub_tasks],
            "parallel_failures": self.parallel_failures,
            "review_passed": self.review_passed,
            "review_score": self.review_score,
            "status": self.status,
            "loop_count": self.loop_count,
            "error_count": self.error_count,
        }


# ====== 检查点持久化 ======


async def save_checkpoint(state: OrchestratorState, db_path: str) -> bool:
    """保存调度状态到 SQLite 检查点"""
    import sqlite3
    try:
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.execute(
            """CREATE TABLE IF NOT EXISTS orchestration_checkpoints (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_hash TEXT UNIQUE,
                state_json TEXT,
                updated_at TEXT
            )"""
        )
        # 生成唯一 task_hash
        import hashlib
        task_hash = hashlib.md5(state.task.encode()).hexdigest()[:16]

        state_data = {
            "task": state.task,
            "task_complexity": state.task_complexity,
            "assigned_models": state.assigned_models,
            "sub_tasks": [s.to_dict() for s in state.sub_tasks],
            "error_count": state.error_count,
            "loop_count": state.loop_count,
            "status": state.status,
        }
        state_json = json.dumps(state_data, ensure_ascii=False)

        conn.execute(
            """INSERT OR REPLACE INTO orchestration_checkpoints
               (task_hash, state_json, updated_at) VALUES (?, ?, ?)""",
            (task_hash, state_json, datetime.now().isoformat()),
        )
        conn.commit()
        conn.close()
        logger.debug(f"Checkpoint saved: {task_hash}")
        return True
    except Exception as e:
        logger.warning(f"Failed to save checkpoint: {e}")
        return False


async def load_checkpoint(task: str, db_path: str) -> Optional[dict]:
    """从 SQLite 加载检查点恢复状态"""
    import sqlite3
    import hashlib
    try:
        if not os.path.exists(db_path):
            return None
        task_hash = hashlib.md5(task.encode()).hexdigest()[:16]
        conn = sqlite3.connect(db_path)
        cursor = conn.execute(
            "SELECT state_json FROM orchestration_checkpoints WHERE task_hash=?",
            (task_hash,),
        )
        row = cursor.fetchone()
        conn.close()
        if row:
            state = json.loads(row[0])
            logger.info(f"Checkpoint loaded: {task_hash} (status={state.get('status')})")
            return state
        return None
    except Exception as e:
        logger.warning(f"Failed to load checkpoint: {e}")
        return None


# ====== 统一 LLM 调用接口 ======


async def call_llm(
    model_name: str,
    messages: list[dict],
    temperature: float = 0.3,
    max_tokens: int = 4096,
    retry_count: int = 2,
) -> str:
    """
    统一异步 LLM 调用（内置指数退避重试 + 错误降级）

    实际部署时集成 litellm / Ollama SDK。此处为 mock 实现。
    """
    logger.debug(f"call_llm: model={model_name}, messages={len(messages)}, retry={retry_count}")

    # 模拟重试/延迟
    for attempt in range(retry_count + 1):
        try:
            # ===== 模拟不同模型的响应 =====
            if "challenge" in model_name or "judge" in model_name:
                # 审查模型
                return (
                    "VERDICT: PASS\n"
                    "证据充分性评分: 85\n"
                    "正反双方均基于证据论证，结论与源码和日志吻合。"
                )

            if "analyzer" in model_name or model_name in ("qwen2", "gpt-4o-mini"):
                # 任务分析器
                return json.dumps({
                    "complexity": "moderate",
                    "suggested_workers": 2,
                    "requires_evidence": True,
                    "reasoning": "任务需要分析源码和日志，复杂度中等",
                }, ensure_ascii=False)

            if "planner" in model_name or model_name in ("claude-sonnet-4", "gpt-4.1"):
                # 规划器
                return (
                    "## 执行计划\n\n"
                    "### 子任务列表\n"
                    "1. **S001** - 加载源码上下文，分析关键函数\n"
                    "2. **S002** - 加载运行时日志，查找错误模式\n"
                    "3. **S003** - 交叉验证源码和日志，确定根因\n"
                    "4. **S004** - 生成修复方案\n"
                )

            # 通用 Worker 模拟
            user_msg = ""
            for m in reversed(messages):
                if m["role"] == "user":
                    user_msg = m["content"]
                    break
            return (
                f"## 执行结果\n\n"
                f"基于提供的证据，完成任务描述中的要求。\n"
                f"引用源码相关行和日志时间戳完成交叉验证。\n\n"
                f"确定性：高（证据一致）"
            )

        except Exception as e:
            if attempt < retry_count:
                delay = 2 ** attempt
                logger.warning(f"LLM call failed (attempt {attempt+1}): {e}, retrying in {delay}s")
                await asyncio.sleep(delay)
            else:
                raise

    return "[LLM call failed]"


# ====== 模型池与资源分配器 ======


class ModelPool:
    """
    模型池 —— 管理所有可用模型及其状态

    职责:
    - 维护本地/云端模型列表
    - 检查模型可用性
    - 根据复杂度和成本选择最优模型
    - 自动降级
    """

    def __init__(self, config: Optional[ModelPoolConfig] = None):
        self.config = config or ModelPoolConfig()
        self._availability: dict[str, bool] = {}  # model_name -> available

        # 初始化所有模型为可用
        for models in [
            self.config.elite_models,
            self.config.strong_models,
            self.config.medium_models,
            self.config.local_models,
        ]:
            for m in models:
                self._availability[m] = True

    def is_available(self, model: str) -> bool:
        """检查模型是否可用"""
        return self._availability.get(model, False)

    def mark_unavailable(self, model: str) -> None:
        """标记模型不可用（触发自动降级）"""
        if model in self._availability:
            self._availability[model] = False
            logger.warning(f"Model marked unavailable: {model}")

    def mark_available(self, model: str) -> None:
        """恢复模型可用状态"""
        if model in self._availability:
            self._availability[model] = True

    def get_available_models(self) -> list[str]:
        """获取当前所有可用模型"""
        return [m for m, avail in self._availability.items() if avail]

    def select_for_complexity(
        self,
        complexity: str,
        require_local: bool = False,
    ) -> dict[str, str]:
        """
        根据复杂度智能分配 Planner 和 Workers

        规则:
        - complex → Planner=elite, Workers=medium+local
        - moderate → Planner=strong, Workers=light+local
        - simple → Planner=light, Workers=local

        自动降级：如果首选不可用，顺延到下一位
        """
        # 先拿 Planner
        def _first_available(candidates: list[str]) -> Optional[str]:
            for m in candidates:
                if self.is_available(m):
                    return m
            return None

        planner = None
        workers = []

        if complexity == "complex":
            planner = _first_available(self.config.elite_models)
            if not planner:
                planner = _first_available(self.config.strong_models)
            # Workers：混合本地和云端 medium
            workers = [
                m for m in self.config.medium_models if self.is_available(m)
            ]
            local_avail = [m for m in self.config.local_models if self.is_available(m)]
            workers.extend(local_avail[:2])
            if not workers:
                workers = [_first_available(self.config.strong_models)] or ["mock-model"]

        elif complexity == "moderate":
            planner = _first_available(self.config.strong_models)
            if not planner:
                planner = _first_available(self.config.medium_models)
            workers = [
                m for m in self.config.local_models if self.is_available(m)
            ]
            if not workers:
                workers = [_first_available(self.config.medium_models)] or ["mock-model"]

        else:  # simple
            planner = _first_available(self.config.local_models)
            if not planner:
                planner = _first_available(self.config.medium_models)
            workers = [planner]  # 简单任务用同一个模型

        # 降级保障
        if not planner:
            planner = "mock-model"
        if not workers:
            workers = ["mock-model"]

        result = {
            "planner": planner,
            "workers": workers,
        }

        logger.info(
            f"Resource allocation: complexity={complexity}, "
            f"planner={planner}, workers={workers}"
        )

        return result

    def get_fallback_chain(self, failed_model: str) -> list[str]:
        """
        获取某个模型失败后的完整降级链

        按层级从低到高尝试：local → light → medium → strong → elite
        """
        if failed_model in self.config.local_models:
            chain = self.config.medium_models + self.config.strong_models + self.config.elite_models
        elif failed_model in self.config.medium_models:
            chain = self.config.strong_models + self.config.elite_models
        elif failed_model in self.config.strong_models:
            chain = self.config.elite_models
        else:
            chain = self.config.strong_models + self.config.elite_models

        return [m for m in chain if self.is_available(m)] or ["mock-model"]


# ====== 系统提示词常量 ======

SYSTEM_PROMPT_ANALYZER = """
你是一个任务分析专家。请分析用户的任务，并输出 JSON 格式的分析结果。

分析维度:
1. 任务复杂度：simple（简单的单步操作）/ moderate（需要多步，有明确路径）/ complex（需要深入分析，有不确定性）
2. 建议 worker 数量：根据任务复杂度建议 1-5 个
3. 是否需要证据：如果涉及代码或系统问题，需要源码和日志
4. 理由：简要说明复杂度判断依据

输出格式（仅 JSON，不要其他内容）：
{"complexity": "simple|moderate|complex", "suggested_workers": 2, "requires_evidence": true, "reasoning": "..."}
""".strip()

SYSTEM_PROMPT_PLANNER = """
你是一个任务拆解专家。请将任务拆解为原子性子任务。

规则:
- 每个子任务必须有唯一 ID (S001, S002, ...)
- 每个子任务必须有明确的输出规范
- 必须注明是否需要源码或日志证据
- 必须按执行顺序排列

输出格式：
## 执行计划
### 子任务列表
1. **ID** - 描述
2. **ID** - 描述
...
""".strip()

SYSTEM_PROMPT_WORKER = """
你是一个专注的执行者。你的职责是完成指定子任务。

## 实事求是法则（你必须遵守）：
1. 你只能基于提供的源码和日志做出结论
2. 每一条断言必须引用具体代码行号或日志时间戳
3. 禁止使用"可能是"、"一般来说"、"按理说"等猜测性表述
4. 如果不确定，如实说明，不要强行给出答案
5. 如果你需要额外信息才能完成，请明确说明需要什么
6. 严格遵守"知不知，尚矣"——知道自己不知道，比不知道更可贵
""".strip()

SYSTEM_PROMPT_REVIEWER = """
## 实事求是裁决法则（最高的审核标准）：

你是最终的审查者。你的裁决必须体现"知不知，尚矣"的智慧。

### 你必须遵守的规则：
1. 你唯一的裁决依据是：是否严格基于给定的源码和日志
2. 如果证据不足以支持结论，裁决为 REJECT
3. 知不知，上。必须诚实评估确定性，严禁掩饰不确定性
4. 输出格式：
   VERDICT: PASS|REJECT
   证据充分性评分: 0-100
   不足之处: <如果评分低于 80，必须说明>
""".strip()


# ====== 智能调度总控 ======


class IntelligentOrchestrator:
    """
    智能调度总控（LangGraph StateGraph 实现）

    《灵枢》的大脑。调度本地和云端多模型、多Agent，
    动态自适应，保证任何任务的最终完成。

    节点:
      1. task_analyzer —— 分析复杂度
      2. resource_allocator —— 分配 Planner + Workers
      3. plan_decomposer —— 拆解子任务
      4. parallel_executor —— 并发执行子任务（核心）
      5. review_and_debate —— 实事求是审查
      6. checkpoint_saver —— 检查点持久化
      7. final_synthesizer —— 综合输出

    循环与容错:
      - review 失败 → 重新执行 failed 子任务
      - worker 失败 → 降级模型重试
      - 检查点每阶段保存 → 断点恢复
    """

    def __init__(
        self,
        config: Optional[OrchestratorConfig] = None,
        model_pool: Optional[ModelPool] = None,
        call_llm_fn: Optional[Callable] = None,
    ):
        self.config = config or OrchestratorConfig()
        self.model_pool = model_pool or ModelPool(self.config.model_pool)
        self._call_llm = call_llm_fn or call_llm
        self._compiled: Optional[CompiledStateGraph] = None

    # ====== 节点实现 ======

    async def _node_task_analyzer(self, state: OrchestratorState) -> dict:
        """
        节点 1: 任务分析

        使用快速模型分析复杂度，输出 JSON 结构。
        """
        logger.info(f"Node: task_analyzer")

        model = self.config.analyzer_model
        if model == "auto":
            # 自动选择：优先本地
            avail = self.model_pool.get_available_models()
            model = next(
                (m for m in ["qwen2", "gpt-4o-mini"] if m in avail),
                "gpt-4o-mini",
            )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT_ANALYZER},
            {"role": "user", "content": f"请分析以下任务：\n\n{state.task}"},
        ]

        result = await self._call_llm(model, messages, temperature=0.2)

        # 解析 JSON
        complexity = "moderate"
        suggested_workers = 2
        requires_evidence = True

        try:
            parsed = json.loads(result)
            complexity = parsed.get("complexity", "moderate")
            suggested_workers = parsed.get("suggested_workers", 2)
            requires_evidence = parsed.get("requires_evidence", True)
        except (json.JSONDecodeError, Exception):
            # 尝试提取 JSON
            json_match = re.search(r"\{.*?\}", result, re.DOTALL)
            if json_match:
                try:
                    parsed = json.loads(json_match.group())
                    complexity = parsed.get("complexity", "moderate")
                except Exception:
                    pass

        logger.info(
            f"Task analysis: complexity={complexity}, "
            f"workers={suggested_workers}, evidence={requires_evidence}"
        )

        return {
            "task_complexity": complexity,
            "loop_count": state.loop_count + 1,
        }

    async def _node_resource_allocator(self, state: OrchestratorState) -> dict:
        """
        节点 2: 智能资源分配

        纯逻辑节点。按复杂度 + 可用性分配模型。
        """
        logger.info(f"Node: resource_allocator")

        assigned = self.model_pool.select_for_complexity(
            complexity=state.task_complexity or "moderate",
        )

        logger.info(
            f"Allocated: planner={assigned['planner']}, "
            f"workers={assigned['workers']}"
        )

        return {"assigned_models": assigned}

    async def _node_plan_decomposer(self, state: OrchestratorState) -> dict:
        """
        节点 3: 任务拆解

        使用 Planner 模型将任务拆为子任务列表。
        """
        logger.info(f"Node: plan_decomposer")

        planner = state.assigned_models.get("planner", "gpt-4o-mini")

        # 构建证据上下文
        evidence_context = ""
        if state.code_context or state.runtime_log:
            evidence_context = (
                "\n## 可用证据\n"
                + (f"\n### 源码\n{state.code_context[:1000]}" if state.code_context else "")
                + (f"\n### 日志\n{state.runtime_log[:1000]}" if state.runtime_log else "")
            )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT_PLANNER},
            {
                "role": "user",
                "content": (
                    f"请将以下任务拆解为子任务列表。\n\n"
                    f"## 任务描述\n{state.task}\n"
                    f"\n## 复杂任务上下文\n"
                    f"任务复杂度：{state.task_complexity}\n"
                    f"可用 Worker 数量：{len(state.assigned_models.get('workers', []))}\n"
                    f"{evidence_context}"
                ),
            },
        ]

        result = await self._call_llm(planner, messages, temperature=0.3)

        # 解析子任务（从 Planner 返回值中提取）
        sub_tasks = []
        lines = result.split("\n")
        task_counter = 0
        for line in lines:
            # 匹配 "N. **S00X** - ..." 或 "N. ID - ..."
            match = re.match(
                r"\d+\.\s+\*{0,2}(S\d+)\*{0,2}\s*-\s*(.+)", line
            )
            if match:
                task_counter += 1
                sub_tasks.append(SubTask(
                    id=match.group(1),
                    description=match.group(2).strip(),
                ))

        # 如果没有解析出任何子任务，生成默认
        if not sub_tasks:
            sub_tasks = [
                SubTask(id="S001", description=f"分析任务：{state.task[:100]}"),
                SubTask(id="S002", description="执行核心操作"),
                SubTask(id="S003", description="验证和输出结果"),
            ]

        logger.info(f"Decomposed into {len(sub_tasks)} sub-tasks")
        for st in sub_tasks:
            logger.info(f"  {st.id}: {st.description[:60]}")

        return {
            "sub_tasks": sub_tasks,
            "execution_plan": {"raw_plan": result, "count": len(sub_tasks)},
        }

    async def _node_parallel_executor(self, state: OrchestratorState) -> dict:
        """
        节点 4: 并行执行器（核心）

        使用 asyncio.gather 多 Agent 并发：
        - 每个 Worker 执行一个子任务
        - 失败自动降级重试
        - 不中断其他 Worker
        """
        logger.info(f"Node: parallel_executor ({len(state.sub_tasks)} sub-tasks)")

        workers = state.assigned_models.get("workers", [])
        if not workers:
            workers = ["mock-model"]

        max_concurrent = self.config.model_pool.max_concurrent_workers

        async def _execute_one(st: SubTask) -> SubTask:
            """执行单个子任务，带降级重试"""
            st.status = "running"

            # 为这个子任务选择 Worker（轮询分配）
            worker_idx = hash(st.id) % len(workers)
            model = workers[worker_idx]
            st.assigned_model = model

            max_retry = self.config.model_pool.max_retries_per_task
            last_error = ""

            for attempt in range(max_retry + 1):
                try:
                    # 构建 Worker prompt
                    evidence_msgs = []
                    if state.code_context:
                        evidence_msgs.append(
                            f"## 证据 A - 源代码\n{state.code_context[:2000]}"
                        )
                    if state.runtime_log:
                        evidence_msgs.append(
                            f"## 证据 B - 运行时日志\n{state.runtime_log[:2000]}"
                        )

                    evidence_block = "\n\n".join(evidence_msgs) if evidence_msgs else "无证据"

                    messages = [
                        {"role": "system", "content": SYSTEM_PROMPT_WORKER},
                        {
                            "role": "user",
                            "content": (
                                f"## 子任务\n{st.id}: {st.description}\n\n"
                                f"## 父任务\n{state.task[:500]}\n\n"
                                f"{evidence_block}"
                            ),
                        },
                    ]

                    result = await self._call_llm(
                        model,
                        messages,
                        temperature=0.3,
                        retry_count=1,
                    )

                    st.result = result
                    st.status = "done"
                    st.retry_count = attempt
                    logger.info(f"  {st.id} DONE (model={model})")
                    return st

                except Exception as e:
                    last_error = str(e)
                    logger.warning(
                        f"  {st.id} FAILED attempt {attempt+1}/{max_retry}: {e}"
                    )

                    if attempt < max_retry:
                        # 降级：找备选模型
                        fallback_chain = self.model_pool.get_fallback_chain(model)
                        if fallback_chain:
                            model = fallback_chain[0]
                            st.assigned_model = model
                            logger.info(f"  {st.id} fallback to {model}")
                            await asyncio.sleep(2 ** attempt)
                        else:
                            break

            st.status = "failed"
            st.error = f"Failed after {max_retry} retries: {last_error}"
            st.retry_count = max_retry
            logger.error(f"  {st.id} FAILED permanently: {last_error}")
            return st

        # 并发执行所有子任务
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _with_semaphore(st: SubTask) -> SubTask:
            async with semaphore:
                return await _execute_one(st)

        results = await asyncio.gather(
            *[_with_semaphore(st) for st in state.sub_tasks],
            return_exceptions=True,
        )

        completed_tasks = []
        failures = 0

        for i, r in enumerate(results):
            if isinstance(r, Exception):
                state.sub_tasks[i].status = "failed"
                state.sub_tasks[i].error = str(r)
                failures += 1
            elif isinstance(r, SubTask):
                completed_tasks.append(r)
                if r.status == "failed":
                    failures += 1

        logger.info(
            f"Parallel execution done: "
            f"{len(completed_tasks) - failures} succeeded, {failures} failed"
        )

        return {
            "parallel_failures": failures,
        }

    async def _node_review_and_debate(self, state: OrchestratorState) -> dict:
        """
        节点 5: 实事求是审查 + 辩论

        使用 Judge 模型审查所有子任务结果。
        遵循"知不知"最高原则，仅基于证据判断。
        """
        logger.info(f"Node: review_and_debate")

        # 收集所有子任务结果
        all_results = []
        for st in state.sub_tasks:
            if st.status == "done":
                all_results.append(f"### {st.id} ({st.assigned_model})\n{st.result[:500]}")
            elif st.status == "failed":
                all_results.append(f"### {st.id} FAILED\n{st.error}")

        results_block = "\n\n".join(all_results) if all_results else "无执行结果"

        evidence_block = ""
        if state.code_context:
            evidence_block += f"\n源码：{state.code_context[:500]}..."
        if state.runtime_log:
            evidence_block += f"\n日志：{state.runtime_log[:500]}..."

        judge_model = next(
            (m for m in self.config.model_pool.strong_models if self.model_pool.is_available(m)),
            self.config.model_pool.strong_models[0],
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT_REVIEWER},
            {
                "role": "user",
                "content": (
                    f"请审查以下执行结果。\n\n"
                    f"## 原始任务\n{state.task[:300]}\n\n"
                    f"## 子任务结果\n{results_block}\n\n"
                    f"## 证据\n{evidence_block}"
                ),
            },
        ]

        result = await self._call_llm(
            judge_model, messages, temperature=0.2
        )

        # 解析审查结果
        passed = False
        score = 0.0

        for line in result.split("\n"):
            if "VERDICT" in line and "PASS" in line.upper():
                passed = True
            if "评分" in line:
                nums = re.findall(r"\d+", line)
                if nums:
                    score = float(nums[0])

        # 如果 failed 任务 > 0，自动设为 REJECT
        if state.parallel_failures > 0:
            passed = False
            if score == 0:
                score = 30.0

        logger.info(f"Review: {'PASS' if passed else 'REJECT'}, score={score}")

        return {
            "review_passed": passed,
            "review_score": score,
            "review_feedback": result[:500],
        }

    async def _node_checkpoint_saver(self, state: OrchestratorState) -> dict:
        """
        节点 6: 检查点持久化

        每个阶段完成后保存状态到 SQLite。
        """
        logger.debug(f"Node: checkpoint_saver")
        await save_checkpoint(state, self.config.checkpoint_db)
        return {}

    async def _node_final_synthesizer(self, state: OrchestratorState) -> dict:
        """
        节点 7: 最终综合输出

        使用 Planner 模型综合所有子任务成果为一份 Markdown 报告。
        """
        logger.info(f"Node: final_synthesizer")

        planner = state.assigned_models.get("planner", "gpt-4o-mini")

        # 构建子任务摘要
        sub_summaries = []
        for st in state.sub_tasks:
            status_mark = "✓" if st.status == "done" else "✗"
            sub_summaries.append(
                f"{status_mark} **{st.id}** ({st.assigned_model}): {st.description[:80]}\n"
                f"  - 状态: {st.status}\n"
                f"  - 结果: {(st.result or '')[:100]}"
            )

        sub_block = "\n".join(sub_summaries)

        if state.review_passed:
            conclusion = (
                f"## 执行摘要\n\n"
                f"任务 \"{state.task[:100]}...\" 已完成。\n\n"
                f"### 调度信息\n"
                f"- 复杂度评估: {state.task_complexity}\n"
                f"- Planner 模型: {planner}\n"
                f"- Worker 模型: {state.assigned_models.get('workers', [])}\n"
                f"- 子任务数: {len(state.sub_tasks)}\n"
                f"- 失败数: {state.parallel_failures}\n"
                f"- 审查评分: {state.review_score}/100\n\n"
                f"### 子任务详情\n{sub_block}\n\n"
                f"### 审查结论\n{state.review_feedback}\n\n"
                f"---\n"
                f"*Generated by LingShu IntelligentOrchestrator*"
            )
        else:
            conclusion = (
                f"## 执行报告（审查未通过）\n\n"
                f"任务 \"{state.task[:100]}...\" 部分完成但审查未通过。\n\n"
                f"### 审查反馈\n{state.review_feedback}\n\n"
                f"### 问题子任务\n"
            )
            for st in state.sub_tasks:
                if st.status == "failed":
                    conclusion += f"\n✗ **{st.id}**: {st.error}"

            conclusion += (
                f"\n\n### 需要人工介入\n"
                f"审查评分 {state.review_score}/100 低于阈值。\n"
                f"请检查上述问题子任务，修复后重试。"
            )

        return {
            "global_result": conclusion,
            "status": "completed" if state.review_passed else "insufficient_evidence",
        }

    # ====== 图构建 ======

    def _build_graph(self) -> CompiledStateGraph:
        """
        构建 LangGraph StateGraph

        图结构:
          task_analyzer → resource_allocator → plan_decomposer
              → parallel_executor → review_and_debate
              ├─ (通过) → checkpoint_saver → final_synthesizer → END
              └─ (不通过 + 有失败子任务) → parallel_executor (重试循环)
        """
        if not HAS_LANGGRAPH:
            raise ImportError("langgraph is required. pip install langgraph")

        workflow = StateGraph(OrchestratorState)

        # 注册节点
        workflow.add_node("task_analyzer", self._node_task_analyzer)
        workflow.add_node("resource_allocator", self._node_resource_allocator)
        workflow.add_node("plan_decomposer", self._node_plan_decomposer)
        workflow.add_node("parallel_executor", self._node_parallel_executor)
        workflow.add_node("review_and_debate", self._node_review_and_debate)
        workflow.add_node("checkpoint_saver", self._node_checkpoint_saver)
        workflow.add_node("final_synthesizer", self._node_final_synthesizer)

        # 边：线性流程
        workflow.set_entry_point("task_analyzer")
        workflow.add_edge("task_analyzer", "resource_allocator")
        workflow.add_edge("resource_allocator", "plan_decomposer")
        workflow.add_edge("plan_decomposer", "parallel_executor")
        workflow.add_edge("parallel_executor", "review_and_debate")

        # 条件边：审查通过 → 完成，否则重试
        def _route_after_review(state: OrchestratorState) -> str:
            if state.review_passed or state.loop_count >= state.max_loops:
                return "checkpoint_saver"
            # 有失败子任务或审查不通过 → 重试执行
            return "parallel_executor"

        workflow.add_conditional_edges(
            "review_and_debate",
            _route_after_review,
            {
                "checkpoint_saver": "checkpoint_saver",
                "parallel_executor": "parallel_executor",
            },
        )

        workflow.add_edge("checkpoint_saver", "final_synthesizer")
        workflow.add_edge("final_synthesizer", END)

        return workflow.compile(checkpointer=MemorySaver())

    # ====== 外部接口 ======

    def _dict_to_state(self, d: dict) -> OrchestratorState:
        """将 LangGraph 返回的 dict 还原为 OrchestratorState"""
        sub_tasks = []
        for st_data in d.get("sub_tasks", []):
            if isinstance(st_data, dict):
                sub_tasks.append(SubTask(**st_data))
            elif isinstance(st_data, SubTask):
                sub_tasks.append(st_data)

        return OrchestratorState(
            task=d.get("task", ""),
            task_complexity=d.get("task_complexity"),
            assigned_models=d.get("assigned_models", {}),
            execution_plan=d.get("execution_plan"),
            sub_tasks=sub_tasks,
            parallel_failures=d.get("parallel_failures", 0),
            code_context=d.get("code_context", ""),
            runtime_log=d.get("runtime_log", ""),
            code_file_path=d.get("code_file_path", ""),
            log_file_path=d.get("log_file_path", ""),
            review_passed=d.get("review_passed"),
            review_score=d.get("review_score"),
            review_feedback=d.get("review_feedback", ""),
            global_result=d.get("global_result"),
            error_count=d.get("error_count", 0),
            checkpoint_data=d.get("checkpoint_data"),
            loop_count=d.get("loop_count", 0),
            max_loops=d.get("max_loops", self.config.max_loops),
            errors=d.get("errors", []),
            status=d.get("status", "pending"),
            started_at=d.get("started_at", ""),
            completed_at=d.get("completed_at", ""),
        )

    async def run(
        self,
        task: str,
        code_file_path: str = "",
        log_file_path: str = "",
        code_line: Optional[int] = None,
        log_tail: int = 500,
        resume: bool = False,
    ) -> OrchestratorState:
        """
        运行完整的智能调度流程

        参数:
            task: 任务描述
            code_file_path: 源码路径
            log_file_path: 日志路径
            code_line: 源码行号
            log_tail: 日志尾部行数
            resume: 是否从检查点恢复

        返回:
            OrchestratorState 包含所有调度和审查信息
        """
        if not HAS_LANGGRAPH:
            raise ImportError("langgraph is required. pip install langgraph")

        if self._compiled is None:
            self._compiled = self._build_graph()

        # 加载代码和日志上下文
        code_context = ""
        runtime_log = ""
        if code_file_path:
            try:
                with open(code_file_path, "r", encoding="utf-8") as f:
                    lines = f.readlines()
                window = 20
                if code_line:
                    start = max(0, code_line - window - 1)
                    end = min(len(lines), code_line + window)
                    selected = lines[start:end]
                    code_context = "".join(
                        f"{i+start+1}| {l}" for i, l in enumerate(selected)
                    )
                else:
                    code_context = "".join(lines[:200])
            except Exception as e:
                logger.warning(f"Failed to load code: {e}")

        if log_file_path:
            try:
                with open(log_file_path, "r", encoding="utf-8", errors="replace") as f:
                    log_lines = f.readlines()
                tail = log_lines[-log_tail:] if len(log_lines) > log_tail else log_lines
                runtime_log = "".join(tail)
            except Exception as e:
                logger.warning(f"Failed to load log: {e}")

        # 加载检查点
        if resume:
            checkpoint = await load_checkpoint(task, self.config.checkpoint_db)
            if checkpoint:
                logger.info(f"Resuming from checkpoint (status={checkpoint.get('status')})")
                # 部分恢复
                initial_state = OrchestratorState(
                    task=task,
                    task_complexity=checkpoint.get("task_complexity"),
                    assigned_models=checkpoint.get("assigned_models", {}),
                    code_context=code_context,
                    runtime_log=runtime_log,
                    code_file_path=code_file_path,
                    log_file_path=log_file_path,
                    error_count=checkpoint.get("error_count", 0),
                    loop_count=checkpoint.get("loop_count", 0),
                    status=checkpoint.get("status", "pending"),
                    max_loops=self.config.max_loops,
                )
                return initial_state  # 返回部分恢复的状态

        initial_state = OrchestratorState(
            task=task,
            code_context=code_context,
            runtime_log=runtime_log,
            code_file_path=code_file_path,
            log_file_path=log_file_path,
            max_loops=self.config.max_loops,
            started_at=datetime.now().isoformat(),
        )

        logger.info("=" * 60)
        logger.info("IntelligentOrchestrator START")
        logger.info(f"Task: {task[:200]}...")
        logger.info(f"Code: {code_file_path or '(not set)'}")
        logger.info(f"Log:  {log_file_path or '(not set)'}")
        logger.info(f"Pool: {self.model_pool.get_available_models()}")
        logger.info("=" * 60)

        try:
            result_dict = await self._compiled.ainvoke(
                initial_state,
                {"configurable": {"thread_id": f"orch_{datetime.now().timestamp():.0f}"}},
            )
            result_state = self._dict_to_state(result_dict)
            result_state.started_at = initial_state.started_at
            result_state.completed_at = datetime.now().isoformat()

            logger.info(f"Pipeline finished: status={result_state.status}")
            return result_state

        except Exception as e:
            logger.error(f"Pipeline failed: {e}")
            initial_state.errors.append(str(e))
            initial_state.status = "failed"
            initial_state.completed_at = datetime.now().isoformat()
            return initial_state

    def reset(self) -> None:
        """重置调度器"""
        self._compiled = None
        logger.info("IntelligentOrchestrator reset")


# ====== 快捷入口 ======


async def run_smart_orchestration(
    task: str,
    code_file: str = "",
    log_file: str = "",
    code_line: Optional[int] = None,
    log_tail: int = 500,
    verbose: bool = True,
) -> dict:
    """
    一键运行智能调度（快捷入口）

    参数:
        task: 任务描述
        code_file: 源码路径
        log_file: 日志路径
        code_line: 源码行号
        log_tail: 日志尾部行数
        verbose: 详细日志

    返回:
        dict 格式的结果
    """
    if verbose:
        configure_orchestrator_logger("INFO")

    orchestrator = IntelligentOrchestrator()

    result = await orchestrator.run(
        task=task,
        code_file_path=code_file,
        log_file_path=log_file,
        code_line=code_line,
        log_tail=log_tail,
    )

    return result.to_dict()


# ====== 自测 / 演示 ======


async def demo_smart_scheduling() -> dict:
    """演示：智能调度一个中等复杂度的分析任务"""
    import tempfile

    configure_orchestrator_logger("INFO")
    logger.info("=" * 60)
    logger.info("DEMO: 智能调度总控 - 综合演示")
    logger.info("=" * 60)

    # 创建临时文件
    code_src = (
        '# parser.py\n'
        'def parse_input(data):\n'
        '    result = {}\n'
        '    for line in data.split("\\n"):\n'
        '        if "=" in line:\n'
        '            k, v = line.split("=", 1)\n'
        '            result[k.strip()] = v.strip()\n'
        '    return result\n'
        '\n'
        'def main():\n'
        '    raw = get_input()\n'
        '    parsed = parse_input(raw)\n'
        '    print(f"Parsed: {parsed}")\n'
    )
    log_src = (
        "2025-01-15 14:23:15 - INFO - Starting parser\n"
        "2025-01-15 14:23:15 - ERROR - parse_input failed: 'NoneType' object has no attribute 'split'\n"
        "2025-01-15 14:23:15 - WARN - raw input was None\n"
        "2025-01-15 14:23:16 - INFO - Shutting down\n"
    )

    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False, encoding="utf-8") as f:
        f.write(code_src)
        code_path = f.name
    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False, encoding="utf-8") as f:
        f.write(log_src)
        log_path = f.name

    try:
        orch = IntelligentOrchestrator()
        result = await orch.run(
            task="分析 parser.py 中 parse_input 函数崩溃的根因并修复",
            code_file_path=code_path,
            log_file_path=log_path,
        )

        print("\n" + "=" * 60)
        print("调度总控演示结果:")
        print(f"  状态: {result.status}")
        print(f"  复杂度: {result.task_complexity}")
        print(f"  分配模型: {result.assigned_models}")
        print(f"  子任务数: {len(result.sub_tasks)}")
        print(f"  并行失败: {result.parallel_failures}")
        print(f"  审查通过: {result.review_passed}")
        print(f"  审查评分: {result.review_score}")
        print(f"\n最终报告:")
        print(f"  {result.global_result[:300]}...")
        print("=" * 60)

        return result.to_dict()

    finally:
        import os as _os
        _os.unlink(code_path)
        _os.unlink(log_path)


if __name__ == "__main__":
    """
    直接运行此模块执行演示

        py core/orchestration_core.py
    """
    asyncio.run(demo_smart_scheduling())