from pathlib import Path
from struct import unpack


ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "docs" / "product-ui" / "reference" / "penpot"
EXPECTED = {
    "desktop-notes.png": (1440, 1024),
    "desktop-notes-source-deleted.png": (1440, 1024),
    "desktop-notes-projection-failed.png": (1440, 1024),
    "desktop-notes-clear.png": (1440, 1024),
    "desktop-notes-empty.png": (1440, 1024),
    "tablet-notes.png": (1024, 768),
    "tablet-notes-sources.png": (1024, 768),
    "tablet-notes-projection-failed.png": (1024, 768),
    "tablet-notes-empty.png": (1024, 768),
    "mobile-notes.png": (390, 844),
    "mobile-notes-editor.png": (390, 844),
    "mobile-notes-filters.png": (390, 844),
    "mobile-notes-sources.png": (390, 844),
    "mobile-notes-conflict.png": (390, 844),
    "mobile-notes-empty.png": (390, 844),
}


def _png_size(path: Path) -> tuple[int, int]:
    payload = path.read_bytes()
    assert payload[:8] == b"\x89PNG\r\n\x1a\n"
    return unpack(">II", payload[16:24])


def test_notes_penpot_exports_have_exact_viewports():
    assert {path.name for path in REFERENCE.glob("*-notes*.png")} >= set(EXPECTED)
    for name, size in EXPECTED.items():
        assert _png_size(REFERENCE / name) == size


def test_notes_handoff_records_exact_board_names_and_sample_boundary():
    handoff = (ROOT / "docs" / "product-ui" / "penpot-handoff.md").read_text("utf-8")
    for board in (
        "Desktop / Notes / Default",
        "Desktop / Notes / Source deleted",
        "Desktop / Notes / Projection failed",
        "Desktop / Notes / Clear confirm",
        "Desktop / Notes / Empty",
        "Tablet / Notes / Default",
        "Tablet / Notes / Sources drawer",
        "Tablet / Notes / Projection failed",
        "Tablet / Notes / Empty",
        "Mobile / Notes / List",
        "Mobile / Notes / Editor",
        "Mobile / Notes / Filters drawer",
        "Mobile / Notes / Sources drawer",
        "Mobile / Notes / Version conflict",
        "Mobile / Notes / Empty",
    ):
        assert board in handoff
    assert "Notes boards use illustrative sample data only" in handoff
