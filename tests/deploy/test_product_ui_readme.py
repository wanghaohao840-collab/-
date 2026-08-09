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
