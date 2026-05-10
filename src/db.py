# -*- coding: utf-8 -*-
"""Database module for LingShu tests"""

import sqlite3
import logging
from typing import Optional, Any

logger = logging.getLogger(__name__)


class DatabaseManager:
    """
    Database manager with simple CRUD operations.
    Uses synchronous sqlite3 for reliable cross-platform support.
    """

    def __init__(self, db_path: str = ":memory:"):
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def connect(self) -> None:
        """Connect to database"""
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        logger.info(f"DB connected: {self.db_path}")

    def close(self) -> None:
        """Close connection"""
        if self._conn:
            self._conn.close()
            self._conn = None

    def execute(self, sql: str, params: tuple = ()) -> Any:
        """Execute SQL"""
        if not self._conn:
            raise ConnectionError("Database not connected")
        try:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur
        except Exception as e:
            raise Exception(f"Query failed: {e}") from e

    def fetch_all(self, sql: str, params: tuple = ()) -> list[dict]:
        """Fetch all rows"""
        cur = self.execute(sql, params)
        return [dict(r) for r in cur.fetchall()]

    def fetch_one(self, sql: str, params: tuple = ()) -> Optional[dict]:
        """Fetch single row"""
        cur = self.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row else None

    def init_tables(self) -> None:
        """Initialize tables"""
        self.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                active INTEGER DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        self.execute("""
            CREATE TABLE IF NOT EXISTS tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                token TEXT UNIQUE NOT NULL,
                expires_at TEXT,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
        """)
        logger.info("DB tables initialized")


class ConnectionError(Exception):
    """Connection error"""
    pass