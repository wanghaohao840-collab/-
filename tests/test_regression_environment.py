"""Regression harness guards, not application configuration behavior."""

import os
from pathlib import Path
import subprocess
import sys


def test_subprocesses_cannot_implicitly_load_workstation_dotenv(tmp_path):
    dotenv = tmp_path / ".env"
    dotenv.write_text("REGRESSION_DOTENV_SENTINEL=must-not-load\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-c", "\n".join([
            "import os, sys",
            "from dotenv import load_dotenv",
            "assert os.environ.get('PYTHON_DOTENV_DISABLED') == '1'",
            "assert load_dotenv(sys.argv[1]) is False",
            "assert 'REGRESSION_DOTENV_SENTINEL' not in os.environ",
        ]), str(dotenv)],
        cwd=tmp_path, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_harness_discards_application_settings_but_retains_explicit_live_opt_in():
    script = Path(__file__).with_name("conftest.py")
    environment = os.environ | {
        "NEO4J_URI": "bolt://example.invalid:7687",
        "NEO4J_PASSWORD": "fake-private-password",
        "RAG_EMBEDDING_API_KEY": "fake-private-key",
        "LLM_API_KEY": "fake-private-key",
        "DEEPSEEK_API_KEY": "fake-private-key",
        "QDRANT_TEST_URL": "http://example.invalid:6333",
        "NEO4J_TEST_URI": "bolt://example.invalid:7687",
    }
    result = subprocess.run(
        [sys.executable, "-c", "\n".join([
            "import os, runpy, sys",
            "runpy.run_path(sys.argv[1])",
            "assert 'NEO4J_URI' not in os.environ",
            "assert 'NEO4J_PASSWORD' not in os.environ",
            "assert 'RAG_EMBEDDING_API_KEY' not in os.environ",
            "assert 'LLM_API_KEY' not in os.environ",
            "assert 'DEEPSEEK_API_KEY' not in os.environ",
            "assert os.environ['NEO4J_TEST_URI'] == 'bolt://example.invalid:7687'",
            "assert os.environ['QDRANT_TEST_URL'] == 'http://example.invalid:6333'",
        ]), str(script)],
        env=environment, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
