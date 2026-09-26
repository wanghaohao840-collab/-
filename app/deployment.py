"""Explicit deployment mode and role settings, with no local fallback."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal, Mapping, cast
from urllib.parse import urlsplit


DataMode = Literal["local", "distributed"]
ProcessRole = Literal["all", "api", "worker"]


class DeploymentConfigurationError(ValueError):
    """Raised when a selected runtime cannot meet its dependency contract."""


def _positive_int(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        value = int(env.get(name, str(default)))
    except ValueError as exc:
        raise DeploymentConfigurationError(f"{name} must be an integer") from exc
    if value < 1:
        raise DeploymentConfigurationError(f"{name} must be positive")
    return value


@dataclass(frozen=True)
class DeploymentSettings:
    data_mode: DataMode
    process_role: ProcessRole
    database_url: str | None = field(repr=False)
    postgres_pool_min_size: int
    postgres_pool_max_size: int
    s3_endpoint_url: str | None = field(repr=False)
    s3_bucket: str | None
    s3_region: str
    s3_access_key_id: str | None = field(repr=False)
    s3_secret_access_key: str | None = field(repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> DeploymentSettings:
        values = os.environ if env is None else env
        data_mode = values.get("APP_DATA_MODE", "local").strip().lower()
        process_role = values.get("APP_PROCESS_ROLE", "all").strip().lower()
        if data_mode not in {"local", "distributed"}:
            raise DeploymentConfigurationError("APP_DATA_MODE must be local or distributed")
        if process_role not in {"all", "api", "worker"}:
            raise DeploymentConfigurationError("APP_PROCESS_ROLE must be all, api, or worker")
        settings = cls(
            data_mode=cast(DataMode, data_mode),
            process_role=cast(ProcessRole, process_role),
            database_url=values.get("DATABASE_URL") or None,
            postgres_pool_min_size=_positive_int(values, "POSTGRES_POOL_MIN_SIZE", 1),
            postgres_pool_max_size=_positive_int(values, "POSTGRES_POOL_MAX_SIZE", 10),
            s3_endpoint_url=values.get("S3_ENDPOINT_URL") or None,
            s3_bucket=values.get("S3_BUCKET") or None,
            s3_region=values.get("S3_REGION", "us-east-1"),
            s3_access_key_id=values.get("S3_ACCESS_KEY_ID") or None,
            s3_secret_access_key=values.get("S3_SECRET_ACCESS_KEY") or None,
        )
        if settings.postgres_pool_min_size > settings.postgres_pool_max_size:
            raise DeploymentConfigurationError(
                "POSTGRES_POOL_MIN_SIZE cannot exceed POSTGRES_POOL_MAX_SIZE"
            )
        return settings

    def validate(self) -> None:
        if self.data_mode == "local":
            if self.process_role != "all":
                raise DeploymentConfigurationError("local mode requires APP_PROCESS_ROLE=all")
            return
        if self.process_role not in {"api", "worker"}:
            raise DeploymentConfigurationError(
                "distributed mode requires APP_PROCESS_ROLE=api or worker"
            )
        required = {
            "DATABASE_URL": self.database_url,
            "S3_ENDPOINT_URL": self.s3_endpoint_url,
            "S3_BUCKET": self.s3_bucket,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise DeploymentConfigurationError(
                f"distributed mode requires {', '.join(missing)}"
            )
        database = urlsplit(self.database_url)
        if database.scheme != "postgresql" or not database.hostname or database.path in {"", "/"}:
            raise DeploymentConfigurationError("DATABASE_URL must name a PostgreSQL database")
        endpoint = urlsplit(self.s3_endpoint_url)
        if (
            endpoint.scheme not in {"http", "https"} or not endpoint.hostname
            or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment
        ):
            raise DeploymentConfigurationError("S3_ENDPOINT_URL must be an HTTP endpoint without credentials")
        if bool(self.s3_access_key_id) != bool(self.s3_secret_access_key):
            raise DeploymentConfigurationError(
                "S3_ACCESS_KEY_ID and S3_SECRET_ACCESS_KEY must be configured together"
            )

    def safe_summary(self) -> dict[str, object]:
        return {
            "data_mode": self.data_mode,
            "process_role": self.process_role,
            "database_configured": bool(self.database_url),
            "object_store_configured": bool(self.s3_endpoint_url and self.s3_bucket),
            "postgres_pool_min_size": self.postgres_pool_min_size,
            "postgres_pool_max_size": self.postgres_pool_max_size,
            "s3_region": self.s3_region,
        }
