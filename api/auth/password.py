"""密码哈希：bcrypt 加盐，恒定时间校验。"""

from __future__ import annotations

import bcrypt


def hash_password(password: str) -> str:
    """对明文密码做 bcrypt 哈希，返回可存储的 ``$2b$...`` 字符串。"""
    pw = password.encode("utf-8")
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    """恒定时间校验明文与存储哈希；参数异常（如格式错）一律视为失败。"""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False
