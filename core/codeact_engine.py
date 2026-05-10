#!/usr/bin/env python3
"""
CodeActEngine - CodeAct 纯代码执行模式

Agent 直接输出可执行代码动作，而非自然语言指令。
来源：Smolagents CodeAct 理念。
"""
from __future__ import annotations
import ast
import asyncio
import logging
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class CodeActStep:
    """单步 CodeAct 动作"""
    action_type: str  # "python" | "shell" | "file_edit" | "think"
    content: str
    result: str = ""
    error: Optional[str] = None


@dataclass
class CodeActResult:
    """CodeAct 执行结果"""
    steps: list[CodeActStep] = field(default_factory=list)
    final_output: str = ""
    success: bool = False


class CodeActEngine:
    """
    CodeAct 纯代码执行引擎

    Agent 输出结构化的代码块，引擎直接执行并返回结果。
    格式:
      ```python
      print("hello")
      ```
      ```shell
      ls -la
      ```
      ```file_edit path/to/file.py
      # content to write
      ```
    """

    def __init__(self, timeout: int = 30, allow_shell: bool = True):
        self.timeout = timeout
        self.allow_shell = allow_shell
        self._namespace: dict[str, Any] = {}

    async def execute(self, agent_output: str) -> CodeActResult:
        """解析并执行 Agent 输出的代码"""
        result = CodeActResult()
        steps = self._parse_actions(agent_output)

        for step in steps:
            try:
                if step.action_type == "python":
                    output, err = await self._exec_python(step.content)
                    step.result = output
                    step.error = err
                elif step.action_type == "shell":
                    if not self.allow_shell:
                        step.error = "Shell execution disabled"
                    else:
                        output, err = await self._exec_shell(step.content)
                        step.result = output
                        step.error = err
                elif step.action_type == "file_edit":
                    output, err = self._exec_file_edit(step.content)
                    step.result = output
                    step.error = err
                elif step.action_type == "think":
                    step.result = f"[Thought recorded: {len(step.content)} chars]"
                result.steps.append(step)
            except Exception as e:
                step.error = str(e)
                result.steps.append(step)

        result.success = all(s.error is None for s in result.steps)
        result.final_output = "\n".join(
            s.result for s in result.steps if s.result and s.action_type != "think"
        )
        return result

    def _parse_actions(self, text: str) -> list[CodeActStep]:
        """从 Agent 输出中解析代码块"""
        import re
        steps = []

        # Match ```type ... ``` blocks
        pattern = r'```(\w+)(?:\s+(.+?))?\n(.*?)```'
        for match in re.finditer(pattern, text, re.DOTALL):
            action_type = match.group(1).strip()
            first_line = (match.group(2) or "").strip()
            content = match.group(3).strip()

            if action_type in ("python", "py"):
                steps.append(CodeActStep(action_type="python", content=content))
            elif action_type in ("shell", "bash", "sh", "cmd"):
                steps.append(CodeActStep(action_type="shell", content=content))
            elif action_type in ("file_edit", "edit"):
                file_path = first_line
                steps.append(CodeActStep(
                    action_type="file_edit",
                    content=f"{file_path}\n{content}" if file_path else content,
                ))
            elif action_type == "think":
                steps.append(CodeActStep(action_type="think", content=content))
            else:
                # Treat unknown code blocks as python
                steps.append(CodeActStep(action_type="python", content=content))

        if not steps:
            # No code blocks found, treat entire output as thought
            steps.append(CodeActStep(action_type="think", content=text))

        return steps

    async def _exec_python(self, code: str) -> tuple[str, Optional[str]]:
        """执行 Python 代码片段"""
        loop = asyncio.get_event_loop()

        def _run():
            try:
                # Check syntax first
                ast.parse(code)
                # Execute in isolated namespace
                local_ns = self._namespace.copy()
                exec_globals = {"__builtins__": __builtins__}
                exec(code, exec_globals, local_ns)
                self._namespace.update(local_ns)
                return "[Python executed successfully]", None
            except Exception as e:
                return "", str(e)

        output, err = await loop.run_in_executor(None, _run)
        return output, err

    async def _exec_shell(self, cmd: str) -> tuple[str, Optional[str]]:
        """执行 shell 命令"""
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout
            )
            out = stdout.decode(errors="replace")
            err = stderr.decode(errors="replace") if stderr else None
            return out[:2000], (err[:1000] if err else None)
        except asyncio.TimeoutError:
            return "", f"Timed out after {self.timeout}s"
        except Exception as e:
            return "", str(e)

    def _exec_file_edit(self, content: str) -> tuple[str, Optional[str]]:
        """执行文件编辑"""
        import os
        lines = content.split("\n", 1)
        if len(lines) < 2:
            return "", "Invalid format: need file_path + content"
        file_path = lines[0].strip()
        file_content = lines[1]

        try:
            os.makedirs(os.path.dirname(os.path.abspath(file_path)) or ".", exist_ok=True)
            with open(file_path, "w") as f:
                f.write(file_content)
            return f"Written to {file_path} ({len(file_content)} bytes)", None
        except Exception as e:
            return "", str(e)

    def reset(self):
        """重置命名空间"""
        self._namespace.clear()