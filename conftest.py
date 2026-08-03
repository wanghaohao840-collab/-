from pathlib import Path
import os


def pytest_configure(config):
    """Keep pytest temp files in a unique workspace-local directory on Windows."""

    if config.option.basetemp is None:
        temp_root = Path.cwd() / ".pytest-tmp-default"
        temp_root.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = str(temp_root / f"run-{os.getpid()}")
