"""
GitIntegration - Git 集成

需求覆盖（第 8 章）：自动 commit、branch 管理、PR 创建
"""
from __future__ import annotations
import os
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class GitIntegration:
    """Git 操作封装"""

    def __init__(self, repo_path: Optional[str] = None):
        self.repo_path = repo_path or os.getcwd()

    def _run_git(self, *args: str) -> tuple[str, str, int]:
        """执行 git 命令"""
        import subprocess
        cmd = ["git", "-C", self.repo_path] + list(args)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            return result.stdout.strip(), result.stderr.strip(), result.returncode
        except Exception as e:
            return "", str(e), -1

    def is_repo(self) -> bool:
        """检查是否为 git 仓库"""
        _, _, code = self._run_git("rev-parse", "--git-dir")
        return code == 0

    def init(self) -> bool:
        """初始化仓库"""
        _, _, code = self._run_git("init")
        return code == 0

    def status(self) -> dict:
        """获取工作区状态"""
        stdout, _, _ = self._run_git("status", "--porcelain")
        files = [line.strip() for line in stdout.split("\n") if line.strip()]
        modified = [f[2:].strip() for f in files if f.startswith(" M") or f.startswith("M")]
        untracked = [f[2:].strip() for f in files if f.startswith("??")]
        staged = [f[2:].strip() for f in files if f.startswith("A ") or f.startswith("M ")]
        return {
            "modified": modified,
            "untracked": untracked,
            "staged": staged,
            "has_changes": len(files) > 0,
        }

    def commit(self, message: str) -> bool:
        """创建提交"""
        self._run_git("add", "-A")
        _, _, code = self._run_git("commit", "-m", message)
        return code == 0

    def create_branch(self, branch_name: str) -> bool:
        """创建并切换到新分支"""
        _, _, code = self._run_git("checkout", "-b", branch_name)
        return code == 0

    def create_pr(self, title: str, body: str = "") -> Optional[str]:
        """创建 PR（需要 gh CLI）"""
        import subprocess
        try:
            result = subprocess.run(
                ["gh", "pr", "create", "--title", title, "--body", body],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode == 0:
                return result.stdout.strip()
            logger.warning(f"PR creation failed: {result.stderr}")
            return None
        except FileNotFoundError:
            logger.warning("gh CLI not found")
            return None

    def diff(self, staged: bool = False) -> str:
        """获取 diff"""
        if staged:
            stdout, _, _ = self._run_git("diff", "--cached")
        else:
            stdout, _, _ = self._run_git("diff")
        return stdout

    def get_log(self, max_count: int = 10) -> list[dict]:
        """获取提交历史"""
        format_str = "%H|%an|%ae|%ai|%s"
        stdout, _, _ = self._run_git(
            "log", f"--max-count={max_count}",
            f"--format={format_str}",
        )
        result = []
        for line in stdout.split("\n"):
            if not line.strip():
                continue
            parts = line.split("|", 4)
            if len(parts) == 5:
                result.append({
                    "hash": parts[0][:8],
                    "author": parts[1],
                    "email": parts[2],
                    "date": parts[3],
                    "message": parts[4],
                })
        return result

    @property
    def current_branch(self) -> str:
        stdout, _, _ = self._run_git("rev-parse", "--abbrev-ref", "HEAD")
        return stdout or "unknown"