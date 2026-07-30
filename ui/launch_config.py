from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping


def _parse_port(value: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            "GRADIO_SERVER_PORT must be an integer from 1 to 65535"
        ) from exc
    if not 1 <= port <= 65535:
        raise ValueError("GRADIO_SERVER_PORT must be an integer from 1 to 65535")
    return port


def _parse_root_path(value: str) -> str | None:
    root_path = value.strip()
    if not root_path:
        return None
    if not root_path.startswith("/"):
        raise ValueError("GRADIO_ROOT_PATH must start with '/'")
    return root_path.rstrip("/") or "/"


@dataclass(frozen=True)
class LaunchConfig:
    server_name: str
    server_port: int
    root_path: str | None
    share: bool = False

    def as_gradio_kwargs(self) -> dict[str, object]:
        kwargs: dict[str, object] = {
            "server_name": self.server_name,
            "server_port": self.server_port,
            "share": self.share,
        }
        if self.root_path is not None:
            kwargs["root_path"] = self.root_path
        return kwargs


def load_launch_config(
    environ: Mapping[str, str] | None = None,
) -> LaunchConfig:
    env = os.environ if environ is None else environ
    server_name = (
        env["GRADIO_SERVER_NAME"]
        if "GRADIO_SERVER_NAME" in env
        else "127.0.0.1"
    ).strip()
    if not server_name:
        raise ValueError("GRADIO_SERVER_NAME cannot be empty")
    port = _parse_port(
        env["GRADIO_SERVER_PORT"]
        if "GRADIO_SERVER_PORT" in env
        else "7860"
    )
    root_path = _parse_root_path(env.get("GRADIO_ROOT_PATH") or "")
    return LaunchConfig(
        server_name=server_name,
        server_port=port,
        root_path=root_path,
    )
