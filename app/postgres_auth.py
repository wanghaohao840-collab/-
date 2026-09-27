"""Authentication against the explicitly migrated PostgreSQL users table."""

from __future__ import annotations

import uuid

from psycopg.errors import UniqueViolation

from app.auth import (AuthError, User, hash_password, normalize_username, utc_now,
                      validate_password, validate_username, verify_password)
from app.postgres import PostgresDatabase


class PostgresAuthService:
    def __init__(self, database: PostgresDatabase):
        self.database = database

    def register(self, username: str, password: str) -> User:
        display, key = validate_username(username)
        validate_password(password)
        user_id = str(uuid.uuid4())
        now = utc_now()
        try:
            with self.database.transaction() as cursor:
                cursor.execute(
                    "insert into users (id,username,username_key,password_hash,status,created_at,updated_at) "
                    "values (%s,%s,%s,%s,'active',%s,%s)",
                    (user_id, display, key, hash_password(password), now, now),
                )
        except UniqueViolation as exc:
            if exc.diag.constraint_name != "users_username_key_key":
                raise
            raise AuthError("Username already exists") from exc
        return User(id=user_id, username=display)

    def authenticate(self, username: str, password: str) -> User:
        _, key = normalize_username(username)
        with self.database.transaction() as cursor:
            row = cursor.execute(
                "select id,username,password_hash from users "
                "where username_key=%s and status='active'", (key,),
            ).fetchone()
        if row is None or not verify_password(password, row["password_hash"]):
            raise AuthError("Invalid username or password")
        return User(id=row["id"], username=row["username"])
