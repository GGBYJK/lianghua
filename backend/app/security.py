from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError


_password_hasher = PasswordHasher()
JWT_SECRET = os.getenv("JWT_SECRET", "change-this-development-secret")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_MINUTES = int(os.getenv("ACCESS_TOKEN_MINUTES", "15"))
REFRESH_TOKEN_DAYS = int(os.getenv("REFRESH_TOKEN_DAYS", "7"))


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def create_access_token(user: dict[str, Any]) -> tuple[str, int]:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=ACCESS_TOKEN_MINUTES)
    payload = {
        "sub": str(user["id"]),
        "username": user["username"],
        "role": user["role"],
        "permissions": user["permissions"],
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": secrets.token_hex(12),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM), ACCESS_TOKEN_MINUTES * 60


def decode_access_token(token: str) -> dict[str, Any]:
    payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    if payload.get("type") != "access":
        raise jwt.InvalidTokenError("invalid token type")
    return payload


def create_refresh_token() -> tuple[str, str, datetime]:
    token = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=REFRESH_TOKEN_DAYS)
    return token, token_hash, expires_at


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()

