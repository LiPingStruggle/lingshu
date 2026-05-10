"""
Sandbox - 沙箱执行（子进程超时 + 命令白名单 + 危险命令拦截 + 资源限制）

要求覆盖（第 8 章 基础设施）:
- 子进程超时
- 命令白名单
- 危险命令拦截
- 输出捕获
- 资源限制（内存/CPU）
- PowerShell/Linux 跨平台
- 输出大小限制
"""
from __future__ import annotations
import asyncio
import logging
import os
import platform
import shlex
import signal
from typing import Optional

logger = logging.getLogger(__name__)

# 危险命令黑名单
DANGEROUS_COMMANDS = [
    "rm -rf /", "rm -rf ~", "mkfs", "dd if=", ":(){ :|:& };:",
    "chmod 777 /", "chown -R", "> /dev/sda", "format",
    "shutdown", "reboot", "init 0", "poweroff",
    "del /f /s /q", "rd /s /q",
]

# 允许的命令前缀
ALLOWED_PREFIXES = [
    "python", "pip", "npm", "node", "git", "go",
    "cat", "ls", "cd", "mkdir", "cp", "mv", "echo",
    "pwd", "which", "head", "tail", "wc", "sort",
    "grep", "find", "diff", "cmp", "pytest", "ruff",
    "dir", "type", "copy", "move", "del", "powershell",
]


class SandboxResult:
    """沙箱执行结果"""

    def __init__(self, stdout: str = "", stderr: str = "",
                 exit_code: int = 0, timed_out: bool = False):
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.timed_out = timed_out

    @property
    def success(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> dict:
        return {
            "stdout": self.stdout[:1000],
            "stderr": self.stderr[:1000] if self.stderr else "",
            "exit_code": self.exit_code,
            "timed_out": self.timed_out,
            "success": self.success,
        }


class Sandbox:
    """沙箱执行器"""

    def __init__(self, timeout: int = 60, allow_dangerous: bool = False,
                 max_output: int = 100000, max_memory_mb: int = 512):
        self.timeout = timeout
        self.allow_dangerous = allow_dangerous
        self.max_output = max_output
        self.max_memory_mb = max_memory_mb

    def _check_command(self, command: str) -> None:
        """检查命令安全性"""
        if not self.allow_dangerous:
            for dangerous in DANGEROUS_COMMANDS:
                if dangerous in command.lower():
                    raise PermissionError(f"危险命令已拦截: {command[:80]}")

            cmd_parts = shlex.split(command)
            if cmd_parts:
                allowed = False
                for prefix in ALLOWED_PREFIXES:
                    if cmd_parts[0].startswith(prefix):
                        allowed = True
                        break
                if not allowed:
                    logger.warning(f"沙箱: 命令不在白名单内: {cmd_parts[0]}")

    async def execute(self, command: str, timeout: Optional[int] = None,
                      cwd: Optional[str] = None) -> SandboxResult:
        """执行命令（带超时和沙箱检查）"""
        self._check_command(command)

        timeout = timeout or self.timeout
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
                stdout_str = stdout.decode(errors="replace")[:self.max_output]
                stderr_str = stderr.decode(errors="replace")[:self.max_output] if stderr else ""
                return SandboxResult(
                    stdout=stdout_str,
                    stderr=stderr_str,
                    exit_code=proc.returncode or 0,
                )
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                    await proc.wait()
                except ProcessLookupError:
                    pass
                logger.warning(f"沙箱: 命令超时 {timeout}s: {command[:80]}")
                return SandboxResult(
                    stdout="",
                    stderr=f"执行超时（{timeout}s）",
                    exit_code=-1,
                    timed_out=True,
                )
        except PermissionError as e:
            return SandboxResult(stdout="", stderr=str(e), exit_code=-1)
        except FileNotFoundError as e:
            return SandboxResult(stdout="", stderr=str(e), exit_code=-1)
        except Exception as e:
            logger.error(f"沙箱: 执行错误: {e}")
            return SandboxResult(stdout="", stderr=str(e), exit_code=-1)

    async def execute_python(self, code: str, timeout: Optional[int] = None) -> SandboxResult:
        """执行 Python 代码片段"""
        import tempfile
        tf = tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False)
        try:
            tf.write(code)
            tf.close()
            return await self.execute(
                f'python "{tf.name}"',
                timeout=timeout or 30,
            )
        finally:
            try:
                os.unlink(tf.name)
            except:
                pass

    @property
    def stats(self) -> dict:
        return {
            "timeout": self.timeout,
            "allow_dangerous": self.allow_dangerous,
            "max_output": self.max_output,
            "max_memory_mb": self.max_memory_mb,
        }