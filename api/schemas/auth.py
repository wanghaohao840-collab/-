from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, SecretStr, field_validator

from app.auth import AuthError, validate_username


class Credentials(BaseModel):
    username: Annotated[str, Field(min_length=3, max_length=64)]
    password: Annotated[SecretStr, Field(min_length=8, max_length=256)]

    @field_validator("username")
    @classmethod
    def validate_username_format(cls, username: str) -> str:
        try:
            validate_username(username)
        except AuthError as exc:
            raise ValueError("invalid username format") from exc
        return username


class SessionResponse(BaseModel):
    username: str
    csrf_token: str
