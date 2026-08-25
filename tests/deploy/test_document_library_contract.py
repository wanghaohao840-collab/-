from pathlib import Path

import pytest
from PIL import Image


ROOT = Path(__file__).parents[2]
REFERENCE_DIR = ROOT / "docs" / "product-ui" / "reference" / "penpot"
SNAPSHOT_DIR = ROOT / "web" / "e2e" / "visual.spec.ts-snapshots"
HANDOFF = ROOT / "docs" / "product-ui" / "penpot-handoff.md"

EXPECTED_EXPORTS = {
    "desktop-documents.png": (1440, 1024),
    "tablet-documents.png": (1024, 768),
    "mobile-documents.png": (390, 844),
    "documents-empty.png": (1440, 1024),
    "documents-importing.png": (1440, 1024),
    "documents-partial-failure.png": (1440, 1024),
    "mobile-import-sheet.png": (390, 844),
}

EXPECTED_BOARDS = (
    "Desktop / Documents / Complete",
    "Tablet / Documents / Complete",
    "Mobile / Documents / Complete",
    "State / Documents / Empty",
    "State / Documents / Importing",
    "State / Documents / Partial failure",
    "Mobile / Documents / Import sheet",
)

EXPECTED_BROWSER_SNAPSHOTS = {
    "documents-empty-desktop.png": (1440, 1024),
    "documents-complete-desktop.png": (1440, 1024),
    "documents-empty-tablet.png": (1024, 768),
    "documents-complete-tablet.png": (1024, 768),
    "documents-empty-mobile.png": (390, 844),
    "documents-complete-mobile.png": (390, 844),
}


def _png_dimensions(path: Path) -> tuple[int, int]:
    with Image.open(path) as image:
        assert image.format == "PNG"
        image.verify()

    with Image.open(path) as image:
        assert image.format == "PNG"
        image.load()
        return image.size


def test_document_library_handoff_names_all_boards_and_reference_exports():
    handoff = HANDOFF.read_text(encoding="utf-8")

    for board in EXPECTED_BOARDS:
        assert board in handoff
    for filename in EXPECTED_EXPORTS:
        assert filename in handoff


def test_document_library_reference_export_set_is_exact():
    document_exports = {
        path.name
        for path in REFERENCE_DIR.glob("*.png")
        if "documents" in path.name or path.name == "mobile-import-sheet.png"
    }

    assert document_exports == set(EXPECTED_EXPORTS)


def test_document_library_reference_exports_are_real_original_size_pngs():
    for filename, dimensions in EXPECTED_EXPORTS.items():
        path = REFERENCE_DIR / filename
        assert path.stat().st_size > 1000
        assert _png_dimensions(path) == dimensions


def test_document_library_browser_snapshot_set_is_exact():
    browser_snapshots = {
        path.name for path in SNAPSHOT_DIR.glob("documents-*.png")
    }

    assert browser_snapshots == set(EXPECTED_BROWSER_SNAPSHOTS)


def test_document_library_browser_snapshots_are_real_viewport_size_pngs():
    for filename, dimensions in EXPECTED_BROWSER_SNAPSHOTS.items():
        path = SNAPSHOT_DIR / filename
        assert path.stat().st_size > 1000
        assert _png_dimensions(path) == dimensions


def test_document_library_png_decoder_rejects_truncated_or_corrupt_data(tmp_path):
    source = REFERENCE_DIR / "desktop-documents.png"
    source_bytes = source.read_bytes()
    truncated = tmp_path / source.name
    truncated.write_bytes(source_bytes[:24])

    corrupt = tmp_path / f"corrupt-{source.name}"
    corrupt_bytes = bytearray(source_bytes)
    corrupt_bytes[len(corrupt_bytes) // 2] ^= 0xFF
    corrupt.write_bytes(corrupt_bytes)

    for invalid_png in (truncated, corrupt):
        with pytest.raises((OSError, SyntaxError)):
            _png_dimensions(invalid_png)
