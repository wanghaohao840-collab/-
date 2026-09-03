from pathlib import Path


ROOT = Path(__file__).parents[2]


def test_root_readme_documents_product_ui_workflow():
    source = (ROOT / "README.md").read_text(encoding="utf-8")

    required_text = (
        "知研",
        "docs/product-ui/penpot-handoff.md",
        "node scripts/design_tokens.mjs design/tokens/zhiyan.tokens.json web/src/styles/tokens.css",
        "node scripts/design_tokens.mjs --check design/tokens/zhiyan.tokens.json web/src/styles/tokens.css",
        "Set-Location web; npm run dev",
        ".\\venv\\Scripts\\python.exe -m uvicorn server:app --host 127.0.0.1 --port 7860 --workers 1",
        "/legacy",
        "/healthz",
        "docs/product-ui/README.md",
    )

    for text in required_text:
        assert text in source


def test_component_map_validation_installs_web_dependencies_first():
    source = (ROOT / "docs" / "product-ui" / "README.md").read_text(
        encoding="utf-8"
    )

    required_sequence = """Set-Location web
npm ci
Set-Location ..
node --test tests/design/test_design_tokens.mjs tests/design/test_penpot_component_map.mjs"""

    assert required_sequence in source


def test_product_ui_closure_report_is_linked_and_reproducible():
    readme = (ROOT / "docs" / "product-ui" / "README.md").read_text(
        encoding="utf-8"
    )
    report = (
        ROOT / "docs" / "product-ui" / "closure-report-2026-08-09.md"
    ).read_text(encoding="utf-8")

    assert "[产品 UI 闭环报告](closure-report-2026-08-09.md)" in readme

    ordered_steps = (
        "docker compose --env-file .runtime/closure-docker/deploy.env -p zhiyan-closure-20260809 build app qdrant",
        "docker compose --env-file .runtime/closure-docker/deploy.env -p zhiyan-closure-20260809 up -d app qdrant",
        "deploy/smoke_test.py --env-file .runtime/closure-docker/deploy.env",
        "docker compose --env-file .runtime/closure-docker/deploy.env -p zhiyan-closure-20260809 down --remove-orphans",
    )
    positions = [report.index(step) for step in ordered_steps]

    assert positions == sorted(positions)
    for text in (
        "127.0.0.1:17860",
        "10001:10001",
        "1000:1000",
        "--workers 1",
        "StarletteDeprecationWarning",
        "npm audit --omit=dev",
        "Penpot",
    ):
        assert text in report
