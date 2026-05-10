# -*- coding: utf-8 -*-
"""Auth module for LingShu tests"""


class AuthError(Exception):
    """认证异常"""
    pass


class UserExistsError(AuthError):
    """用户已存在"""
    pass


class UserNotFoundError(AuthError):
    """用户不存在"""
    pass


class InvalidCredentialsError(AuthError):
    """凭证无效"""
    pass


_user_store: dict[str, dict] = {}
"""简单内存用户存储: username -> {password, created_at, active}"""


def register(username: str, password: str) -> dict:
    """注册新用户"""
    if not username or len(username) < 3:
        raise AuthError("Username must be at least 3 characters")
    if not password or len(password) < 6:
        raise AuthError("Password must be at least 6 characters")

    if username in _user_store:
        raise UserExistsError(f"User '{username}' already exists")

    from datetime import datetime
    user = {
        "username": username,
        "password": password,
        "created_at": datetime.now().isoformat(),
        "active": True,
        "login_count": 0,
    }
    _user_store[username] = user
    return {k: v for k, v in user.items() if k != "password"}


def login(username: str, password: str) -> dict:
    """用户登录"""
    if username not in _user_store:
        raise UserNotFoundError(f"User '{username}' not found")

    user = _user_store[username]
    if not user["active"]:
        raise AuthError(f"User '{username}' is deactivated")

    if user["password"] != password:
        raise InvalidCredentialsError("Invalid password")

    user["login_count"] += 1
    return {k: v for k, v in user.items() if k != "password"}


def deactivate_user(username: str) -> bool:
    """停用用户"""
    if username not in _user_store:
        raise UserNotFoundError(f"User '{username}' not found")
    _user_store[username]["active"] = False
    return True


def list_users() -> list[dict]:
    """列出所有用户（脱敏）"""
    return [{k: v for k, v in u.items() if k != "password"} for u in _user_store.values()]


def _reset_store() -> None:
    """测试用：重置用户存储"""
    _user_store.clear()