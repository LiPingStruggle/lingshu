"""
SessionManager - 会话管理

需求覆盖（第 8 章）：
- CLI 关闭后恢复会话
- 跨调用保持上下文
"""
from __future__ import annotations
import json
import os
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class Message:
    role: str  # user | assistant | system
    content: str
    timestamp: float = 0.0

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = time.time()


@dataclass
class Session:
    session_id: str
    messages: list[Message] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0

    def __post_init__(self):
        now = time.time()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now


class SessionManager:
    """会话管理器"""

    def __init__(self, session_dir: str = ".lingshu/sessions"):
        self.session_dir = session_dir
        self._sessions: dict[str, Session] = {}
        os.makedirs(session_dir, exist_ok=True)
        self._load_all()

    def _session_path(self, session_id: str) -> str:
        return os.path.join(self.session_dir, f"{session_id}.json")

    def _load_all(self) -> None:
        if not os.path.isdir(self.session_dir):
            return
        for fname in os.listdir(self.session_dir):
            if fname.endswith(".json"):
                path = os.path.join(self.session_dir, fname)
                try:
                    with open(path, "r") as f:
                        data = json.load(f)
                    session = Session(
                        session_id=data["session_id"],
                        messages=[Message(**m) for m in data.get("messages", [])],
                        metadata=data.get("metadata", {}),
                        created_at=data.get("created_at", 0),
                        updated_at=data.get("updated_at", 0),
                    )
                    self._sessions[session.session_id] = session
                except Exception as e:
                    logger.warning(f"SessionManager: failed to load {fname}: {e}")

    def create_session(self, session_id: str, metadata: Optional[dict] = None) -> Session:
        session = Session(
            session_id=session_id,
            metadata=metadata or {},
        )
        self._sessions[session_id] = session
        self._save(session)
        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        return self._sessions.get(session_id)

    def add_message(self, session_id: str, role: str, content: str) -> None:
        session = self._sessions.get(session_id)
        if not session:
            return
        session.messages.append(Message(role=role, content=content))
        session.updated_at = time.time()
        self._save(session)

    def get_context(self, session_id: str, max_messages: int = 20) -> list[Message]:
        session = self._sessions.get(session_id)
        if not session:
            return []
        return session.messages[-max_messages:]

    def _save(self, session: Session) -> None:
        path = self._session_path(session.session_id)
        try:
            with open(path, "w") as f:
                json.dump({
                    "session_id": session.session_id,
                    "messages": [
                        {"role": m.role, "content": m.content, "timestamp": m.timestamp}
                        for m in session.messages
                    ],
                    "metadata": session.metadata,
                    "created_at": session.created_at,
                    "updated_at": session.updated_at,
                }, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"SessionManager: save failed: {e}")

    def delete_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)
        path = self._session_path(session_id)
        if os.path.exists(path):
            os.remove(path)

    @property
    def stats(self) -> dict:
        return {
            "active_sessions": len(self._sessions),
            "total_messages": sum(len(s.messages) for s in self._sessions.values()),
        }