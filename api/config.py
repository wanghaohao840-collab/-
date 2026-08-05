from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ApiConfig:
    cookie_name: str = "zhiyan_session"
    cookie_secure: bool = False
    cookie_samesite: str = "lax"

    @classmethod
    def from_environment(cls) -> "ApiConfig":
        return cls(
            cookie_secure=os.getenv("APP_COOKIE_SECURE", "").lower() == "true",
        )
