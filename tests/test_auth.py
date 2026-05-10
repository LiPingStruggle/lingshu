#!/usr/bin/env python3
"""Tests for auth module"""
import pytest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.auth import (
    register, login, deactivate_user, list_users,
    _reset_store, AuthError, UserExistsError,
    UserNotFoundError, InvalidCredentialsError,
)


class TestAuth:
    """Test the auth functions"""

    def setup_method(self):
        _reset_store()

    def test_register_success(self):
        """Test successful registration"""
        user = register("alice", "secret123")
        assert user["username"] == "alice"
        assert user["active"] is True
        assert "password" not in user

    def test_register_duplicate(self):
        """Test duplicate registration"""
        register("bob", "password123")
        with pytest.raises(UserExistsError):
            register("bob", "another456")

    def test_login_success(self):
        """Test successful login"""
        register("charlie", "mypassword")
        result = login("charlie", "mypassword")
        assert result["username"] == "charlie"
        assert result["login_count"] >= 1

    def test_login_invalid_credentials(self):
        """Test login with wrong password"""
        register("dave", "correctpw")
        with pytest.raises(InvalidCredentialsError):
            login("dave", "wrongpw")

    def test_login_nonexistent_user(self):
        """Test login with nonexistent user (BUG002 regression)"""
        with pytest.raises(UserNotFoundError):
            login("nonexistent", "any")

    def test_deactivate_user(self):
        """Test deactivating a user"""
        register("eve", "password123")
        deactivate_user("eve")
        with pytest.raises(AuthError, match="deactivated"):
            login("eve", "password123")

    def test_list_users(self):
        """Test listing users"""
        register("user1", "pass123456")
        register("user2", "pass789012")
        users = list_users()
        assert len(users) >= 2
        assert all("password" not in u for u in users)

    def test_register_short_username(self):
        """Test registration with too short username"""
        with pytest.raises(AuthError, match="at least 3 characters"):
            register("ab", "password123")

    def test_register_short_password(self):
        """Test registration with too short password"""
        with pytest.raises(AuthError, match="at least 6 characters"):
            register("validuser", "short")

    def test_username_case_sensitivity(self):
        """Test username case sensitivity"""
        register("Alice", "password123")
        # Different case should be a different user
        register("alice", "password456")
        users = list_users()
        usernames = [u["username"] for u in users]
        assert "Alice" in usernames
        assert "alice" in usernames