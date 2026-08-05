from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, SecretStr


class Credentials(BaseModel):
    username: Annotated[str, Field(min_length=3, max_length=64)]
    password: Annotated[SecretStr, Field(min_length=8, max_length=256)]


class SessionResponse(BaseModel):
    username: str
    csrf_token: str
