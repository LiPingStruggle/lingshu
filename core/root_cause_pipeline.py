#!/usr/bin/env python3
# 灵枢 (LingShu) - 道法自然，实事求是的根因分析管道
"""
EvidenceBasedRootCausePipeline - 源码+日志双驱动、零猜想、实事求是的根因分析

核心理念:
  "知不知，尚矣；不知知，病也。" ——《道德经》
  "实事求是" —— 一切结论必须同时得到源码逻辑和日志现象的双重证据支撑。
  审查过程恪守"言有宗，事有君"，证据不足时坦然承认未知，绝不妄言。

设计哲学:
  1. 双证据驱动：每个 LLM 节点同时注入源码上下文 + 运行时日志
  2. 零猜想约束：反幻觉监控器检测"可能是、一般来说、按理说"等猜测性表述
  3. 辩论求真：Challenger 只能基于证据质疑，Judge 恪守"知不知"原则
  4. 证据充分性评分：< 80 分时必须明确说明证据不足之处

架构:
  load_evidences → free_analysis → uncertainty_monitor ─┬─→ final_output
                                                         └─→ structured_debate (子图)
                                                               ├─ adversarial_challenge
                                                               ├─ proposer_response
                                                               └─ judge
"""
from __future__ import annotations
import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Optional, Callable, Any

logger = logging.getLogger("lingshu.root_cause")

# ====== LangGraph 导入 ======
try:
    from langgraph.graph import StateGraph, END
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph.state import CompiledStateGraph
    HAS_LANGGRAPH = True
except ImportError:
    HAS_LANGGRAPH = False
    StateGraph = object  # type: ignore
    END = "__end__"
    MemorySaver = object  # type: ignore
    CompiledStateGraph = object  # type: ignore

# ====== 日志配置 ======

def configure_root_cause_logger(
    level: str = "DEBUG",
    log_file: Optional[str] = None,
) -> logging.Logger:
    """
    配置根因分析专用日志器

    参数:
        level: 日志级别 (DEBUG/INFO/WARNING/ERROR)
        log_file: 可选日志文件路径，不指定则只输出到 stderr

    返回:
        配置完成的 logger 实例
    """
    fmt = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    root_logger = logging.getLogger("lingshu.root_cause")
    root_logger.setLevel(getattr(logging, level.upper(), logging.DEBUG))

    # 清除已有 handlers 避免重复
    root_logger.handlers.clear()

    # stderr handler
    stderr = logging.StreamHandler()
    stderr.setFormatter(logging.Formatter(fmt))
    root_logger.addHandler(stderr)

    # 可选文件 handler
    if log_file:
        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setFormatter(logging.Formatter(fmt))
        root_logger.addHandler(fh)

    return root_logger


# ====== 证据加载工具 ======

def load_code_context(file_path: str, line: Optional[int] = None) -> str:
    """
    加载源代码上下文

    参数:
        file_path: 源码文件路径
        line: 可选，指定行号（含前后各 20 行）

    返回:
        带行号的源码文本片段
    """
    if not os.path.isfile(file_path):
        logger.warning(f"Code file not found: {file_path}")
        return f"[文件未找到: {file_path}]"

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        logger.error(f"Failed to read {file_path}: {e}")
        return f"[读取失败: {e}]"

    window = 20  # 上下文窗口行数
    if line is not None:
        start = max(0, line - window - 1)
        end = min(len(lines), line + window)
        # line 参数是 1-indexed
        selected = lines[start:end]
        prefix = f"{file_path}:{line} (附近 {start+1}-{end} 行)\n"
        return prefix + "".join(
            f"{i+start+1:6d}| {l}" for i, l in enumerate(selected)
        )
    else:
        # 全文返回，限制最多 200 行
        max_lines = 200
        selected = lines[:max_lines]
        prefix = f"{file_path} (前 {min(len(lines), max_lines)} 行)\n"
        return prefix + "".join(
            f"{i+1:6d}| {l}" for i, l in enumerate(selected)
        )


def load_runtime_log(log_path: str, tail_lines: int = 500) -> str:
    """
    加载运行时日志尾部

    参数:
        log_path: 日志文件路径
        tail_lines: 末尾行数，默认 500

    返回:
        带时间戳的日志文本
    """
    if not os.path.isfile(log_path):
        logger.warning(f"Log file not found: {log_path}")
        return f"[日志文件未找到: {log_path}]"

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        logger.error(f"Failed to read log {log_path}: {e}")
        return f"[读取日志失败: {e}]"

    # 取尾部
    tail = lines[-tail_lines:] if len(lines) > tail_lines else lines

    # 尝试过滤噪声行
    filtered = []
    noise_patterns = [
        r"^\s*$",  # 空行
        r"^\s*#",  # 注释
        r"heartbeat", r"__debug__",  # 调试噪声
    ]
    for line in tail:
        line_s = line.strip()
        if any(re.search(p, line_s, re.IGNORECASE) for p in noise_patterns):
            filtered.append(f"# (noise filtered)\n")
        else:
            filtered.append(line)

    result = f"{log_path} (末 {len(tail)} 行, 总共 {len(lines)} 行)\n"
    return result + "".join(filtered)


def build_evidence_messages(
    code_context: str,
    runtime_log: str,
) -> list[dict]:
    """
    构建证据注入系统消息

    所有 LLM 节点的 messages 中必须包含此消息，
    确保分析始终基于证据，禁止凭空猜测。
    """
    return [
        {
            "role": "system",
            "content": (
                "## 你必须基于以下两份证据进行分析，严禁凭空猜测：\n"
                "\n"
                "### 证据 A - 源代码：\n"
                f"<code>{code_context}</code>"
                "\n"
                "### 证据 B - 运行时日志：\n"
                f"<log>{runtime_log}</log>"
            ),
        }
    ]


# ====== 反幻觉规范常量 ======

# 猜测性表述模式 —— 监控器将检测这些关键词
GUESS_PATTERNS: list[re.Pattern] = [
    re.compile(r"可能是"),
    re.compile(r"也许"),
    re.compile(r"说不定"),
    re.compile(r"一般来说"),
    re.compile(r"经验上"),
    re.compile(r"常见做法是"),
    re.compile(r"按理说"),
    re.compile(r"大概率"),
    re.compile(r"推测"),
    re.compile(r"猜想"),
    re.compile(r"应该是"),
    re.compile(r"不出意外的话"),
    re.compile(r"typical"),
    re.compile(r"usually"),
    re.compile(r"probably"),
    re.compile(r"it stands to reason"),
]

# 系统提示词：实事求是审查法则（反向验证者 Challenger）
CHALLENGER_SYSTEM_PROMPT = ("""
## 实事求是审查法则（你必须遵守）：

你是一个严格的证据审查者。你的唯一职责是验证分析结论是否严格基于给定的源码和日志。

### 你必须遵守的规则：
1. 你只能依据给出的源码和日志提出质疑，不得引用你"认为应该这样"的外部知识。
2. 如果你找不到任何逻辑漏洞或证据矛盾，必须明确说："经过严格审查，未发现偏离证据的结论"，不得为反对而反对。
3. 任何质疑，必须指向具体代码行或日志条目。
4. 禁止使用"这不合常理"、"正常来说"等脱离具体证据的表述。
5. 如果证据不足以支撑某个断言，明确指出证据缺口。
""").strip()

# 系统提示词：实事求是裁决法则（裁判 Judge）
JUDGE_SYSTEM_PROMPT = ("""
## 实事求是裁决法则（最高的审核标准）：

你是最终的裁决者。你的裁决必须体现"知不知，尚矣"的智慧。

### 你必须遵守的规则：
1. 你唯一的裁决依据是：正反双方的论据，哪一方更严格地基于给定的源码和日志。
2. 如果双方都缺乏决定性证据，你必须裁决为 REJECT，并明确说明："证据不足以支持任何确定结论，需要人工介入或补充更多信息。"
3. 知不知，上。你必须诚实评估整个分析的确定性，严禁用华丽的辞藻掩盖不确定性。
4. 你的输出必须包含"证据充分性评分（0-100）"，如果低于 80，必须给出"证据不足之处"的具体说明。
5. 裁决结果必须是 PASS 或 REJECT。
""").strip()

# ====== 状态定义 ======


@dataclass
class PipelineState:
    """EvidenceBasedRootCausePipeline 的完整状态"""
    # 任务描述
    task_description: str

    # 证据（双驱动）
    code_file_path: str = ""
    code_line: Optional[int] = None
    log_file_path: str = ""
    log_tail_lines: int = 500
    code_context: str = ""
    runtime_log: str = ""

    # 分析结果
    free_analysis_result: Optional[str] = None
    uncertainty_detected: bool = False
    uncertainty_reasons: list[str] = field(default_factory=list)
    deep_analysis_result: Optional[str] = None

    # 辩论子图结果
    debate_passed: Optional[bool] = None
    debate_evidence_score: Optional[float] = None  # 证据充分性评分 0-100
    debate_detail: str = ""

    # 最终输出
    final_conclusion: Optional[str] = None
    final_evidence_index: dict = field(default_factory=dict)

    # 循环控制
    loop_count: int = 0
    max_loops: int = 2

    # 最终元数据
    errors: list[str] = field(default_factory=list)
    status: str = "pending"  # pending | completed | failed | insufficient_evidence

    def to_dict(self) -> dict:
        return {
            "task_description": self.task_description,
            "code_file_path": self.code_file_path,
            "log_file_path": self.log_file_path,
            "free_analysis": (self.free_analysis_result or "")[:500],
            "uncertainty_detected": self.uncertainty_detected,
            "uncertainty_reasons": self.uncertainty_reasons,
            "deep_analysis": (self.deep_analysis_result or "")[:500],
            "debate_passed": self.debate_passed,
            "debate_evidence_score": self.debate_evidence_score,
            "conclusion": (self.final_conclusion or "")[:500],
            "status": self.status,
            "loop_count": self.loop_count,
        }


# ====== 工具函数：异步 LLM 调用 ======


async def call_llm(
    model_name: str,
    messages: list[dict],
    **kwargs: Any,
) -> str:
    """
    统一的异步 LLM 调用接口

    实际项目中替换为 litellm 调用。此处为 mock 实现，
    返回模拟响应以支持离线验证和测试。

    参数:
        model_name: 模型名称
        messages: 消息列表
        **kwargs: 额外参数（temperature, max_tokens 等）

    返回:
        LLM 响应文本
    """
    logger.debug(f"call_llm: model={model_name}, messages_count={len(messages)}")

    # 提取最后一条用户/assistant 消息作为 prompt
    user_msg = ""
    for m in reversed(messages):
        if m["role"] in ("user", "assistant"):
            user_msg = m["content"]
            break

    # 提取证据消息
    evidence_text = ""
    for m in messages:
        if m["role"] == "system" and "证据 A" in m["content"]:
            evidence_text = m["content"]
            break

    # ===== 模拟分析响应 =====
    # 路由优先级：全名匹配 > 前缀匹配

    if "challenge" in model_name:
        # Challenger 模拟
        if evidence_text and evidence_text.strip():
            return (
                "经过严格审查，未发现偏离证据的结论。\n"
                "分析中引用的代码行 xxx 与日志时间戳 yyy 一致。\n"
                "未检测到逻辑矛盾。"
            )
        return (
            "质疑：分析结论声称 X 是根因，但日志中没有直接错误行与之对应。\n"
            "具体引用：日志末尾第 10 行显示的是 INFO 级别的正常输出，未见异常。"
        )

    if "judge" in model_name:
        # Judge 模拟
        if not evidence_text or not evidence_text.strip():
            return (
                "VERDICT: REJECT\n"
                "证据充分性评分: 30\n"
                "证据不足：未提供源码和日志证据，无法做出任何确定结论。\n"
                "需要人工介入或补充更多信息。"
            )
        return (
            "VERDICT: PASS\n"
            "证据充分性评分: 85\n"
            "证据充分性分析：正反双方均基于给定证据进行了论证，\n"
            "结论与源码行 42 以及日志时间戳 14:23:15.123 吻合。\n"
            "不足之处：缺少更多历史日志样本以排除偶然性。"
        )

    if "deep" in model_name:
        # 深度分析模拟（严格基于证据）
        return (
            "## 根因分析\n\n"
            "基于证据 A（源码第 42 行：`result = process(data)`）和"
            "证据 B（日志 14:23:15.123 行：`ERROR - process failed: type mismatch`）：\n\n"
            "根因：函数 `process()` 在第 42 行接收了类型不匹配的参数，导致运行时错误。\n"
            "日志 14:23:15.123 直接验证了此错误的发生。\n\n"
            "确定性：高（双重证据交叉验证一致）"
        )

    # 自由分析模拟（故意带猜测性表述 + 缺乏证据引用，供 uncertainty_monitor 检测）
    return (
        "## 初步分析\n\n"
        "根据提供的源码和日志，可能的问题是 `process()` 函数处理了空值。\n"
        "一般来说这种问题可以通过添加空值检查解决。\n\n"
        "建议进一步检查调用链。"
    )


# ====== 反幻觉监控器 ======


def _detect_guesswork(text: str) -> list[str]:
    """
    检测文本中的猜测性表述

    扫描所有 GUESS_PATTERNS，返回匹配的猜测模式列表。
    空列表表示未检测到猜测。
    """
    found = []
    for pattern in GUESS_PATTERNS:
        matches = pattern.findall(text)
        for m in matches:
            found.append(m)
    return found


def _check_evidence_citation(text: str) -> bool:
    """
    检查结论是否引用了具体代码行号或日志时间戳

    如果文本中没有任何行号引用（如 "第 xx 行"、":xx"）
    或日志时间戳引用，视为缺乏证据支撑。
    """
    has_line_ref = bool(re.search(r"(?:行|line)\s*[:：]?\s*\d+", text, re.IGNORECASE))
    has_timestamp_ref = bool(
        re.search(r"\d{1,2}:\d{2}:\d{2}(?:\.\d+)?", text)
    )
    return has_line_ref or has_timestamp_ref


def uncertainty_monitor_logic(
    free_analysis: str,
    code_context: str,
    runtime_log: str,
) -> tuple[bool, list[str]]:
    """
    不确定性 + 反幻觉双重扫描

    返回:
        (detected: bool, reasons: list[str])
    """
    reasons = []

    # 1. 检测猜测性表述
    guess_phrases = _detect_guesswork(free_analysis)
    if guess_phrases:
        reasons.append(f"检测到猜测性表述: {', '.join(set(guess_phrases))}")

    # 2. 检查证据引用
    if not _check_evidence_citation(free_analysis):
        reasons.append("结论中未引用具体代码行号或日志时间戳，缺乏证据支撑")

    # 3. 检查是否提及了证据本身
    if code_context and "源码" not in free_analysis and "code" not in free_analysis.lower():
        reasons.append("分析未引用提供的源代码证据")
    if runtime_log and "日志" not in free_analysis and "log" not in free_analysis.lower():
        reasons.append("分析未引用提供的运行时日志证据")

    detected = len(reasons) > 0
    if detected:
        logger.warning(f"Uncertainty detected: {reasons}")

    return detected, reasons


# ====== 管道构建 ======


class EvidenceBasedRootCausePipeline:
    """
    基于证据的根因分析管道（LangGraph StateGraph 实现）

    核心流程:
      1. load_evidences —— 加载源码上下文 + 运行时日志
      2. free_analysis —— 强模型基于证据自由分析
      3. uncertainty_monitor —— 纯逻辑双重扫描（不确定 + 反幻觉）
      4. structured_debate —— 辩论子图（触发时进入）
      5. final_output —— 输出结论 + 证据索引

    设计原则:
      - 一切结论必须基于双重证据（源码 + 日志）
      - 严禁猜测，证据不足时坦然承认
      - 可配置的证据路径，适配不同项目
    """

    def __init__(
        self,
        analysis_model: str = "analysis_model",
        challenge_model: str = "challenge_model",
        judge_model: str = "judge_model",
        deep_model: str = "deep_analysis_model",
        call_llm_fn: Optional[Callable] = None,
        max_loops: int = 2,
    ):
        """
        初始化根因分析管道

        参数:
            analysis_model: 自由分析阶段模型
            challenge_model: 辩论 Challenger 模型
            judge_model: 辩论 Judge 模型
            deep_model: 深度分析模型
            call_llm_fn: 自定义 LLM 调用函数（默认使用内部 mock）
            max_loops: 最大辩论循环次数
        """
        self.analysis_model = analysis_model
        self.challenge_model = challenge_model
        self.judge_model = judge_model
        self.deep_model = deep_model
        self._call_llm = call_llm_fn or call_llm
        self.max_loops = max_loops

        # LangGraph 编译后的图 (lazy init)
        self._compiled: Optional[CompiledStateGraph] = None

    def _dict_to_state(self, d: dict) -> PipelineState:
        """将 LangGraph 返回的 dict 还原为 PipelineState"""
        # PipelineState 的 dataclass 字段与 dict key 一致
        return PipelineState(
            task_description=d.get("task_description", ""),
            code_file_path=d.get("code_file_path", ""),
            code_line=d.get("code_line"),
            log_file_path=d.get("log_file_path", ""),
            log_tail_lines=d.get("log_tail_lines", 500),
            code_context=d.get("code_context", ""),
            runtime_log=d.get("runtime_log", ""),
            free_analysis_result=d.get("free_analysis_result"),
            uncertainty_detected=d.get("uncertainty_detected", False),
            uncertainty_reasons=d.get("uncertainty_reasons", []),
            deep_analysis_result=d.get("deep_analysis_result"),
            debate_passed=d.get("debate_passed"),
            debate_evidence_score=d.get("debate_evidence_score"),
            debate_detail=d.get("debate_detail", ""),
            final_conclusion=d.get("final_conclusion"),
            final_evidence_index=d.get("final_evidence_index", {}),
            loop_count=d.get("loop_count", 0),
            max_loops=d.get("max_loops", self.max_loops),
            errors=d.get("errors", []),
            status=d.get("status", "pending"),
        )

    # ====== 节点函数 ======

    async def _node_load_evidences(self, state: PipelineState) -> dict:
        """
        节点: 加载证据

        从配置的路径加载源码和日志，注入到状态中。
        如果路径未配置，记录警告但继续（允许部分证据模式）。
        """
        logger.info("Node: load_evidences")

        code_context = ""
        runtime_log = ""

        if state.code_file_path:
            code_context = load_code_context(state.code_file_path, state.code_line)
            logger.info(f"Loaded code context: {state.code_file_path}:{state.code_line or 'full'}")
        else:
            logger.warning("No code file path configured")

        if state.log_file_path:
            runtime_log = load_runtime_log(state.log_file_path, state.log_tail_lines)
            logger.info(f"Loaded runtime log: {state.log_file_path} (tail {state.log_tail_lines})")
        else:
            logger.warning("No log file path configured")

        return {
            "code_context": code_context,
            "runtime_log": runtime_log,
        }

    async def _node_free_analysis(self, state: PipelineState) -> dict:
        """
        节点: 自由分析

        强模型基于源码和日志证据进行自由分析。
        所有 messages 中注入证据系统消息。
        """
        logger.info("Node: free_analysis")

        evidence_msgs = build_evidence_messages(state.code_context, state.runtime_log)

        messages = evidence_msgs + [
            {
                "role": "user",
                "content": (
                    f"请分析以下问题，并严格基于给出的证据（源码 + 日志）做出结论。\n\n"
                    f"## 问题描述\n{state.task_description}\n\n"
                    f"## 要求\n"
                    f"1. 逐条引用证据（代码行号 / 日志时间戳）\n"
                    f"2. 指出确定性程度\n"
                    f"3. 如果不确定，务必如实说明\n"
                    f"4. 禁止凭空猜测"
                ),
            }
        ]

        result = await self._call_llm(self.analysis_model, messages, temperature=0.2)

        return {
            "free_analysis_result": result,
            "loop_count": state.loop_count + 1,
        }

    async def _node_uncertainty_monitor(self, state: PipelineState) -> dict:
        """
        节点: 不确定性 + 反幻觉监控（纯逻辑，不调用 LLM）

        双重扫描:
        1. 检测猜测性表述（"可能是、一般来说"等）
        2. 检查证据引用（行号、时间戳）

        如果检测到问题，标记 uncertainty_detected=True 并记录原因，
        图路由到 debate 子图进行纠正。
        """
        logger.info("Node: uncertainty_monitor")

        if not state.free_analysis_result:
            return {"uncertainty_detected": False, "uncertainty_reasons": []}

        detected, reasons = uncertainty_monitor_logic(
            state.free_analysis_result,
            state.code_context,
            state.runtime_log,
        )

        if detected:
            logger.warning(f"uncertainty_monitor fired: {reasons}")
        else:
            logger.info("uncertainty_monitor: all clear (no guesswork detected)")

        return {
            "uncertainty_detected": detected,
            "uncertainty_reasons": reasons,
        }

    async def _node_deep_analysis(self, state: PipelineState) -> dict:
        """
        节点: 深度分析（反幻觉触发后调用）

        使用更强的模型或更严格的提示，重新分析。
        """
        logger.info(f"Node: deep_analysis (loop={state.loop_count})")

        evidence_msgs = build_evidence_messages(state.code_context, state.runtime_log)

        # 注入反幻觉提醒
        warning = ""
        if state.uncertainty_reasons:
            warning = (
                "\n\n## 上轮分析存在以下问题，必须避免：\n"
                + "\n".join(f"- {r}" for r in state.uncertainty_reasons)
                + "\n\n你必须严格基于证据，逐行引用代码和日志，禁止猜测。"
            )

        messages = evidence_msgs + [
            {
                "role": "user",
                "content": (
                    f"请深度分析以下问题。注意：你必须严格基于证据。\n\n"
                    f"## 问题描述\n{state.task_description}\n\n"
                    f"## 上轮初步分析\n{state.free_analysis_result or '无'}\n"
                    f"{warning}"
                ),
            }
        ]

        result = await self._call_llm(self.deep_model, messages, temperature=0.1)

        return {"deep_analysis_result": result}

    async def _node_debate_challenge(self, state: PipelineState) -> str:
        """
        辩论子图节点: 反向验证 (Adversarial Challenge)

        Challenger 基于实事求是法则审查分析结论。
        """
        logger.info("Debate node: adversarial_challenge")

        analysis = state.deep_analysis_result or state.free_analysis_result or ""
        evidence_msgs = build_evidence_messages(state.code_context, state.runtime_log)

        messages = [
            {"role": "system", "content": CHALLENGER_SYSTEM_PROMPT},
            evidence_msgs[0],
            {
                "role": "user",
                "content": (
                    f"以下是一份基于证据的分析结论，请你作为审查者严格验证：\n\n"
                    f"## 分析结论\n{analysis}\n\n"
                    f"## 你的任务\n"
                    f"1. 逐一校验结论中的每一条断言是否都有源码或日志证据支撑\n"
                    f"2. 指出任何超出证据范围的断言\n"
                    f"3. 如果全部通过，请明确声明"
                ),
            },
        ]

        result = await self._call_llm(
            self.challenge_model, messages, temperature=0.3
        )

        return result

    async def _node_debate_judge(self, state: PipelineState, challenge: str) -> dict:
        """
        辩论子图节点: 裁判 (Judge)

        恪守"知不知"原则，基于正反双方论据做出最终裁决。
        """
        logger.info("Debate node: judge")

        analysis = state.deep_analysis_result or state.free_analysis_result or ""
        evidence_msgs = build_evidence_messages(state.code_context, state.runtime_log)

        messages = [
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            evidence_msgs[0],
            {
                "role": "user",
                "content": (
                    f"请你作为最终裁判，基于实事求是原则做出裁决。\n\n"
                    f"## 分析结论（正方）\n{analysis}\n\n"
                    f"## 审查意见（反方）\n{challenge}\n\n"
                    f"## 你的裁决\n"
                    f"1. VERDICT: PASS 或 REJECT\n"
                    f"2. 证据充分性评分（0-100）\n"
                    f"3. 证据不足之处的具体说明（如果评分 < 80）"
                ),
            },
        ]

        result = await self._call_llm(self.judge_model, messages, temperature=0.2)

        # 解析裁决
        passed = False
        score = 0.0

        for line in result.split("\n"):
            if "VERDICT" in line and "PASS" in line.upper():
                passed = True
            if "评分" in line:
                nums = re.findall(r"\d+", line)
                if nums:
                    score = float(nums[0])

        logger.info(f"Judge verdict: {'PASS' if passed else 'REJECT'}, score={score}")

        return {
            "debate_passed": passed,
            "debate_evidence_score": score,
            "debate_detail": result,
        }

    async def _node_final_output(self, state: PipelineState) -> dict:
        """
        节点: 最终输出

        汇总所有阶段结果，生成可追踪的结论 + 证据索引。
        """
        logger.info("Node: final_output")

        final_analysis = state.deep_analysis_result or state.free_analysis_result or ""

        evidence_index = {
            "code_file": state.code_file_path or "未配置",
            "code_line": state.code_line,
            "code_length": len(state.code_context),
            "log_file": state.log_file_path or "未配置",
            "log_tail_lines": state.log_tail_lines,
            "log_length": len(state.runtime_log),
        }

        # 确定最终状态
        if state.loop_count >= self.max_loops and state.uncertainty_detected:
            status = "insufficient_evidence"
            conclusion = (
                f"证据不足以支持任何确定结论。\n\n"
                f"经过 {state.loop_count} 轮分析，自由分析和深度分析均存在以下问题：\n"
                + "\n".join(f"- {r}" for r in state.uncertainty_reasons)
                + "\n\n需要人工介入或补充更多信息（如更完整的日志、复现步骤等）。"
            )
        elif state.debate_passed is False:
            status = "insufficient_evidence"
            conclusion = (
                f"审查未通过：辩论裁判给出证据充分性评分 {state.debate_evidence_score}/100。\n\n"
                f"{state.debate_detail}"
            )
        else:
            status = "completed"
            conclusion = final_analysis

        return {
            "final_conclusion": conclusion,
            "final_evidence_index": evidence_index,
            "status": status,
        }

    # ====== 图构建 ======

    def _build_graph(self) -> CompiledStateGraph:
        """
        构建 LangGraph StateGraph

        图结构:
          load_evidences → free_analysis → uncertainty_monitor
            ├─ (未检测到问题) → final_output
            └─ (检测到问题) → deep_analysis → structured_debate → final_output
        """
        if not HAS_LANGGRAPH:
            raise ImportError(
                "langgraph is required. Install with: pip install langgraph"
            )

        workflow = StateGraph(PipelineState)

        # 注册节点
        workflow.add_node("load_evidences", self._node_load_evidences)
        workflow.add_node("free_analysis", self._node_free_analysis)
        workflow.add_node("uncertainty_monitor", self._node_uncertainty_monitor)
        workflow.add_node("deep_analysis", self._node_deep_analysis)
        workflow.add_node("final_output", self._node_final_output)

        # 辩论子图（内联实现）
        async def debate_subgraph(state: PipelineState) -> dict:
            challenge = await self._node_debate_challenge(state)
            judge_result = await self._node_debate_judge(state, challenge)
            return judge_result

        workflow.add_node("structured_debate", debate_subgraph)

        # 边：linear flow with conditional routing
        workflow.set_entry_point("load_evidences")
        workflow.add_edge("load_evidences", "free_analysis")
        workflow.add_edge("free_analysis", "uncertainty_monitor")

        # 条件路由：uncertainty_monitor → deep_analysis 或 final_output
        def _route_from_monitor(state: PipelineState) -> str:
            if state.uncertainty_detected and state.loop_count < self.max_loops:
                return "deep_analysis"
            return "final_output"

        workflow.add_conditional_edges(
            "uncertainty_monitor",
            _route_from_monitor,
            {"deep_analysis": "deep_analysis", "final_output": "final_output"},
        )

        # 深度分析后总是进入辩论
        workflow.add_edge("deep_analysis", "structured_debate")
        # 辩论后进入最终输出
        workflow.add_edge("structured_debate", "final_output")
        # 最终输出结束
        workflow.add_edge("final_output", END)

        # 编译
        return workflow.compile(checkpointer=MemorySaver())

    # ====== 外部接口 ======

    async def run(
        self,
        task_description: str,
        code_file_path: str = "",
        code_line: Optional[int] = None,
        log_file_path: str = "",
        log_tail_lines: int = 500,
        max_loops: Optional[int] = None,
    ) -> PipelineState:
        """
        运行完整的根因分析管道

        参数:
            task_description: 问题描述
            code_file_path: 源码文件路径
            code_line: 源码行号（可选）
            log_file_path: 日志文件路径
            log_tail_lines: 日志尾部行数
            max_loops: 最大分析循环次数（覆盖默认值）

        返回:
            包含所有阶段结果和最终结论的 PipelineState
        """
        if not HAS_LANGGRAPH:
            raise ImportError("langgraph is required. pip install langgraph")

        if self._compiled is None:
            self._compiled = self._build_graph()

        initial_state = PipelineState(
            task_description=task_description,
            code_file_path=code_file_path,
            code_line=code_line,
            log_file_path=log_file_path,
            log_tail_lines=log_tail_lines,
            max_loops=max_loops or self.max_loops,
        )

        logger.info("=" * 60)
        logger.info(f"EvidenceBasedRootCausePipeline START")
        logger.info(f"Task: {task_description[:200]}...")
        logger.info(f"Code: {code_file_path or '(not set)'}")
        logger.info(f"Log:  {log_file_path or '(not set)'}")
        logger.info("=" * 60)

        try:
            # LangGraph ainvoke 返回 dict，需转换回 PipelineState
            result_dict = await self._compiled.ainvoke(
                initial_state,
                {"configurable": {"thread_id": "root_cause_main"}},
            )
            result_state = self._dict_to_state(result_dict)
            logger.info(f"Pipeline finished: status={result_state.status}")
            return result_state
        except Exception as e:
            logger.error(f"Pipeline failed: {e}")
            initial_state.errors.append(str(e))
            initial_state.status = "failed"
            return initial_state

    async def run_with_evidence_files(
        self,
        task_description: str,
        evidence_config: dict,
    ) -> PipelineState:
        """
        使用配置字典运行管道

        evidence_config 示例:
        {
            "code_file": "src/parser.py",
            "code_line": 42,
            "log_file": "logs/error.log",
            "log_tail": 500,
        }
        """
        return await self.run(
            task_description=task_description,
            code_file_path=evidence_config.get("code_file", ""),
            code_line=evidence_config.get("code_line"),
            log_file_path=evidence_config.get("log_file", ""),
            log_tail_lines=evidence_config.get("log_tail", 500),
        )

    def reset(self) -> None:
        """重置管道（重新编译图，清除状态）"""
        self._compiled = None
        logger.info("Pipeline reset")


# ====== 快捷入口 ======

async def run_root_cause_analysis(
    task_description: str,
    code_file: str = "",
    code_line: Optional[int] = None,
    log_file: str = "",
    log_tail: int = 500,
    analysis_model: str = "analysis",
    challenge_model: str = "challenge",
    judge_model: str = "judge",
    verbose: bool = True,
) -> dict:
    """
    一键运行根因分析（快捷入口）

    参数:
        task_description: 问题描述
        code_file: 源码路径
        code_line: 源码行号
        log_file: 日志路径
        log_tail: 日志尾部行数
        analysis_model: 分析模型
        challenge_model: 挑战模型
        judge_model: 裁判模型
        verbose: 是否输出详细日志

    返回:
        dict 格式的结果
    """
    if verbose:
        configure_root_cause_logger("INFO")

    pipeline = EvidenceBasedRootCausePipeline(
        analysis_model=analysis_model,
        challenge_model=challenge_model,
        judge_model=judge_model,
    )

    result = await pipeline.run(
        task_description=task_description,
        code_file_path=code_file,
        code_line=code_line,
        log_file_path=log_file,
        log_tail_lines=log_tail,
    )

    return result.to_dict()


# ====== 自测 / 演示场景 ======

async def demo_insufficient_evidence() -> dict:
    """
    演示场景：证据不足时系统如何响应

    故意不提供源码和日志路径，触发 uncertainty_monitor → debate →
    Judge 因证据不足要求人类介入。
    """
    configure_root_cause_logger("INFO")
    logger.info("=" * 60)
    logger.info("DEMO: 证据不足场景")
    logger.info("=" * 60)

    pipeline = EvidenceBasedRootCausePipeline()
    result = await pipeline.run(
        task_description="分析用户登录失败的根因",
        # 故意不提供证据路径
        code_file_path="",
        log_file_path="",
    )

    print("\n" + "=" * 60)
    print("演示结果:")
    print(f"  状态: {result.status}")
    print(f"  检测到不确定性: {result.uncertainty_detected}")
    print(f"  原因: {result.uncertainty_reasons}")
    print(f"  辩论通过: {result.debate_passed}")
    print(f"  证据评分: {result.debate_evidence_score}")
    print(f"\n最终结论:")
    print(f"  {result.final_conclusion}")
    print("=" * 60)

    return result.to_dict()


async def demo_full_analysis() -> dict:
    """
    演示场景：完整分析（带模拟证据文件）

    创建一个临时源码文件和日志文件，演示完整流程。
    """
    import tempfile

    configure_root_cause_logger("INFO")
    logger.info("=" * 60)
    logger.info("DEMO: 完整分析场景")
    logger.info("=" * 60)

    # 创建临时源码文件（使用简单字符串避免解析歧义）
    code_src = (
        'import json\n'
        'def process(data):\n'
        '    result = json.loads(data)\n'
        '    return result["value"]\n'
        'def main():\n'
        '    raw = get_input()\n'
        '    val = process(raw)\n'
        '    print(val)\n'
    )
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", delete=False, encoding="utf-8"
    ) as f:
        f.write(code_src)
        code_path = f.name

    # 创建临时日志文件
    log_src = (
        "2025-01-15 14:23:15,123 - INFO - Starting main\n"
        "2025-01-15 14:23:15,200 - ERROR - process failed: type mismatch\n"
        "2025-01-15 14:23:15,201 - WARN - raw input: invalid json\n"
        "2025-01-15 14:23:15,300 - INFO - Shutting down\n"
    )
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".log", delete=False, encoding="utf-8"
    ) as f:
        f.write(log_src)
        log_path = f.name

    try:
        pipeline = EvidenceBasedRootCausePipeline()
        result = await pipeline.run(
            task_description="分析 JSON 解析失败的错误",
            code_file_path=code_path,
            code_line=8,
            log_file_path=log_path,
            log_tail_lines=100,
        )

        print("\n" + "=" * 60)
        print("演示结果:")
        print(f"  状态: {result.status}")
        print(f"  检测到不确定性: {result.uncertainty_detected}")
        print(f"  辩论通过: {result.debate_passed}")
        print(f"  证据评分: {result.debate_evidence_score}")
        print(f"\n最终结论:")
        print(f"  {result.final_conclusion}")
        print(f"\n证据索引:")
        for k, v in result.final_evidence_index.items():
            print(f"  {k}: {v}")
        print("=" * 60)

        return result.to_dict()
    finally:
        import os as _os
        _os.unlink(code_path)
        _os.unlink(log_path)


if __name__ == "__main__":
    """
    直接运行此模块执行演示

        py core/root_cause_pipeline.py
    """
    asyncio.run(demo_insufficient_evidence())
    print("\n\n")
    asyncio.run(demo_full_analysis())