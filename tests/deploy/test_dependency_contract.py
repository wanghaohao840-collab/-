import json
from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_development_requirements_add_httpx2_without_shipping_it_in_docker():
    dev = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    assert "-r requirements.txt" in dev
    assert "httpx2==2.9.1" in dev

    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY requirements.txt /app/requirements.txt" in dockerfile
    assert "requirements-dev.txt" not in dockerfile


def test_frontend_pins_fixed_ajv_directly():
    package = json.loads((ROOT / "web" / "package.json").read_text(encoding="utf-8"))
    assert package["devDependencies"]["ajv"] == "8.20.0"


def test_readme_uses_development_requirements_for_local_setup():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert '-r requirements-dev.txt' in readme
