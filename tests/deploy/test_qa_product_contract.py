from __future__ import annotations

import struct
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
HANDOFF_PATH = REPOSITORY_ROOT / "docs" / "product-ui" / "penpot-handoff.md"
REFERENCE_ROOT = REPOSITORY_ROOT / "docs" / "product-ui" / "reference" / "penpot"
QA_STYLE_PATH = REPOSITORY_ROOT / "web" / "src" / "styles" / "qa.css"

QA_REFERENCES = (
    ("Desktop / QA / Default", "desktop-qa.png", 1440, 1024),
    ("Desktop / QA / Summary running", "desktop-qa-summary.png", 1440, 1024),
    ("Desktop / QA / Delete confirm", "desktop-qa-delete.png", 1440, 1024),
    ("Tablet / QA / Default", "tablet-qa.png", 1024, 768),
    ("Tablet / QA / Sources drawer", "tablet-qa-sources.png", 1024, 768),
    ("Mobile / QA / Default", "mobile-qa.png", 390, 844),
    ("Mobile / QA / Sources sheet", "mobile-qa-sources.png", 390, 844),
    ("Mobile / QA / Failure retry", "mobile-qa-failure.png", 390, 844),
)


def _png_dimensions(path: Path) -> tuple[int, int]:
    payload = path.read_bytes()
    assert payload[:8] == b"\x89PNG\r\n\x1a\n"
    assert payload[12:16] == b"IHDR"
    return struct.unpack(">II", payload[16:24])


def test_qa_penpot_handoff_records_every_responsive_state() -> None:
    handoff = HANDOFF_PATH.read_text(encoding="utf-8")

    assert "## Intelligent QA vertical slice" in handoff
    assert "zero broken component-root links" in handoff
    assert "zero text-bounds overflow" in handoff
    assert "zero actual-bounds overflow" in handoff
    assert "zero mobile targets below 44 × 44" in handoff

    for board_name, filename, _, _ in QA_REFERENCES:
        assert f"`{board_name}`" in handoff
        assert f"reference/penpot/{filename}" in handoff


def test_qa_penpot_exports_are_exact_nonempty_pngs() -> None:
    for _, filename, width, height in QA_REFERENCES:
        path = REFERENCE_ROOT / filename
        assert path.stat().st_size > 1_024
        assert _png_dimensions(path) == (width, height)


def test_qa_supporting_copy_uses_accessible_primary_text_token() -> None:
    stylesheet = QA_STYLE_PATH.read_text(encoding="utf-8")
    selector = ".qa-header p, .qa-muted, .qa-conversation small, .qa-sources small"
    rule = next(line for line in stylesheet.splitlines() if line.startswith(selector))
    assert "var(--color-text-primary)" in rule
    assert "var(--color-text-secondary)" not in rule
