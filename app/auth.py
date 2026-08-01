from __future__ import annotations

import hashlib
import hmac
import secrets
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from app.database import transaction


SCRYPT_N = 16384
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32


class AuthError(ValueError):
    """Raised for expected registration and authentication failures."""


@dataclass(frozen=True)
class User:
    id: str
    username: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_username(username: str) -> tuple[str, str]:
    display = unicodedata.normalize("NFKC", username.strip())
    key = display.casefold()
    return display, key


def validate_username(username: str) -> tuple[str, str]:
    display, key = normalize_username(username)
    if not 3 <= len(display) <= 32:
        raise AuthError("Username must be 3-32 characters")
    if not (display[0].isalnum() and display[-1].isalnum()):
        raise AuthError("Username must start and end with a letter or digit")
    if any(not (ch.isalnum() or ch in {"_", "-", "."}) for ch in display):
        raise AuthError("Username contains unsupported characters")
    return display, key


def validate_password(password: str) -> None:
    if not 8 <= len(password) <= 128:
        raise AuthError("Password must be 8-128 characters")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return (
        f"scrypt$v=1$n={SCRYPT_N}$r={SCRYPT_R}$p={SCRYPT_P}"
        f"$dklen={SCRYPT_DKLEN}$salt={salt.hex()}$hash={derived.hex()}"
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        parts = dict(part.split("=", 1) for part in encoded.split("$")[2:])
        salt = bytes.fromhex(parts["salt"])
        expected = bytes.fromhex(parts["hash"])
        derived = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(parts["n"]),
            r=int(parts["r"]),
            p=int(parts["p"]),
            dklen=int(parts["dklen"]),
        )
    except Exception:
        return False
    return hmac.compare_digest(derived, expected)


class AuthService:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)

    def register(self, username: str, password: str) -> User:
        display, key = validate_username(username)
        validate_password(password)
        user_id = str(uuid.uuid4())
        now = utc_now()
        password_hash = hash_password(password)

        try:
            with transaction(self.db_path) as conn:
                conn.execute(
                    """
                    insert into users (id, username, username_key, password_hash, status, created_at, updated_at)
                    values (?, ?, ?, ?, 'active', ?, ?)
                    """,
                    (user_id, display, key, password_hash, now, now),
                )
        except Exception as exc:
            if "unique" in str(exc).lower():
                raise AuthError("Username already exists") from exc
            raise

        return User(id=user_id, username=display)

    def authenticate(self, username: str, password: str) -> User:
        _, key = normalize_username(username)
        with transaction(self.db_path) as conn:
            row = conn.execute(
                """
                select id, username, password_hash
                from users
                where username_key = ? and status = 'active'
                """,
                (key,),
            ).fetchone()

        if row is None or not verify_password(password, row["password_hash"]):
            raise AuthError("Invalid username or password")

        return User(id=row["id"], username=row["username"])
