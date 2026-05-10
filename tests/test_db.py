#!/usr/bin/env python3
"""Tests for db module"""
import pytest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.db import DatabaseManager


class TestDatabase:
    """Test the DatabaseManager class"""

    def setup_method(self):
        self.db = DatabaseManager(":memory:")

    def test_connect_and_init(self):
        """Test connecting and initializing tables"""
        self.db.connect()
        assert self.db._conn is not None
        self.db.init_tables()
        self.db.close()

    def test_execute_sql(self):
        """Test executing SQL"""
        self.db.connect()
        self.db.init_tables()
        cur = self.db.execute(
            "INSERT INTO users (username, password) VALUES (?, ?)",
            ("alice", "secret123")
        )
        assert cur is not None
        self.db.close()

    def test_fetch_all(self):
        """Test fetching all rows"""
        self.db.connect()
        self.db.init_tables()
        self.db.execute(
            "INSERT INTO users (username, password) VALUES (?, ?)",
            ("bob", "pass123")
        )
        rows = self.db.fetch_all(
            "SELECT * FROM users WHERE username = ?", ("bob",)
        )
        assert len(rows) == 1
        assert rows[0]["username"] == "bob"
        assert rows[0]["password"] == "pass123"
        self.db.close()

    def test_fetch_one(self):
        """Test fetching single row"""
        self.db.connect()
        self.db.init_tables()
        self.db.execute(
            "INSERT INTO users (username, password) VALUES (?, ?)",
            ("charlie", "mypass")
        )
        row = self.db.fetch_one(
            "SELECT * FROM users WHERE username = ?", ("charlie",)
        )
        assert row is not None
        assert row["username"] == "charlie"
        self.db.close()

    def test_fetch_nonexistent(self):
        """Test fetching nonexistent row"""
        self.db.connect()
        self.db.init_tables()
        row = self.db.fetch_one(
            "SELECT * FROM users WHERE username = ?", ("nonexistent",)
        )
        assert row is None
        self.db.close()

    def test_connection_error(self):
        """Test operations without connection raise error"""
        from src.db import ConnectionError
        with pytest.raises(ConnectionError):
            self.db.execute("SELECT 1")

    def test_multiple_users(self):
        """Test multiple user operations"""
        self.db.connect()
        self.db.init_tables()
        users = [("dave", "pass1"), ("eve", "pass2"), ("frank", "pass3")]
        for u, p in users:
            self.db.execute(
                "INSERT INTO users (username, password) VALUES (?, ?)", (u, p)
            )
        rows = self.db.fetch_all("SELECT * FROM users")
        assert len(rows) == 3
        self.db.close()

    def test_init_tables_idempotent(self):
        """Test init_tables can be called multiple times"""
        self.db.connect()
        self.db.init_tables()
        self.db.init_tables()  # Second call should not fail
        self.db.init_tables()  # Third call should not fail
        self.db.close()
        assert True