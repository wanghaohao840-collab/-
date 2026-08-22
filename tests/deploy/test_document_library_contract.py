import struct
from pathlib import Path


ROOT = Path(__file__).parents[2]
REFERENCE_DIR = ROOT / "docs" / "product-ui" / "reference" / "penpot"
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


def _png_dimensions(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert data[12:16] == b"IHDR"
    return struct.unpack(">II", data[16:24])


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
