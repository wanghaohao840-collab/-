import re
from itertools import combinations
from pathlib import Path
from struct import unpack


ROOT = Path(__file__).resolve().parents[2]
REFERENCE = ROOT / "docs" / "product-ui" / "reference" / "penpot"
HANDOFF = ROOT / "docs" / "product-ui" / "penpot-handoff.md"
FINAL_REVISION = 152
EXPECTED_BOARDS = {
    "Desktop / Notes / Default": ("02 Desktop", "9b1e7a6b-703c-8060-8008-7071c3463d87", (1440, 1024), "1099f839-63e4-80b7-8008-90947129dd57", "desktop-notes.png"),
    "Desktop / Notes / Source deleted": ("02 Desktop", "9b1e7a6b-703c-8060-8008-7071c3463d87", (1440, 1024), "1099f839-63e4-80b7-8008-909480e7b254", "desktop-notes-source-deleted.png"),
    "Desktop / Notes / Projection failed": ("02 Desktop", "9b1e7a6b-703c-8060-8008-7071c3463d87", (1440, 1024), "1099f839-63e4-80b7-8008-90948ee9cad4", "desktop-notes-projection-failed.png"),
    "Desktop / Notes / Clear confirm": ("02 Desktop", "9b1e7a6b-703c-8060-8008-7071c3463d87", (1440, 1024), "1099f839-63e4-80b7-8008-90949f99978b", "desktop-notes-clear.png"),
    "Desktop / Notes / Empty": ("02 Desktop", "9b1e7a6b-703c-8060-8008-7071c3463d87", (1440, 1024), "1099f839-63e4-80b7-8008-9094b0a45de7", "desktop-notes-empty.png"),
    "Tablet / Notes / Default": ("03 Tablet", "9b1e7a6b-703c-8060-8008-7071c9876902", (1024, 768), "1099f839-63e4-80b7-8008-90951951e0df", "tablet-notes.png"),
    "Tablet / Notes / Sources drawer": ("03 Tablet", "9b1e7a6b-703c-8060-8008-7071c9876902", (1024, 768), "1099f839-63e4-80b7-8008-9095231ebdf8", "tablet-notes-sources.png"),
    "Tablet / Notes / Projection failed": ("03 Tablet", "9b1e7a6b-703c-8060-8008-7071c9876902", (1024, 768), "1099f839-63e4-80b7-8008-90952f8a7d54", "tablet-notes-projection-failed.png"),
    "Tablet / Notes / Empty": ("03 Tablet", "9b1e7a6b-703c-8060-8008-7071c9876902", (1024, 768), "1099f839-63e4-80b7-8008-90953a56062e", "tablet-notes-empty.png"),
    "Mobile / Notes / List": ("04 Mobile", "9b1e7a6b-703c-8060-8008-7071c9888df9", (390, 844), "1099f839-63e4-80b7-8008-9095af8eb216", "mobile-notes.png"),
    "Mobile / Notes / Editor": ("04 Mobile", "9b1e7a6b-703c-8060-8008-7071c9888df9", (390, 844), "1099f839-63e4-80b7-8008-9095b5a6954a", "mobile-notes-editor.png"),
    "Mobile / Notes / Filters drawer": ("04 Mobile", "9b1e7a6b-703c-8060-8008-7071c9888df9", (390, 844), "1099f839-63e4-80b7-8008-9095bb0121d8", "mobile-notes-filters.png"),
    "Mobile / Notes / Sources drawer": ("04 Mobile", "9b1e7a6b-703c-8060-8008-7071c9888df9", (390, 844), "1099f839-63e4-80b7-8008-9095c19ccceb", "mobile-notes-sources.png"),
    "Mobile / Notes / Version conflict": ("04 Mobile", "9b1e7a6b-703c-8060-8008-7071c9888df9", (390, 844), "1099f839-63e4-80b7-8008-9095ca611a4f", "mobile-notes-conflict.png"),
    "Mobile / Notes / Empty": ("04 Mobile", "9b1e7a6b-703c-8060-8008-7071c9888df9", (390, 844), "1099f839-63e4-80b7-8008-9095d15efe2b", "mobile-notes-empty.png"),
}
SHARED_COMPONENTS = {
    "Button": "9b1e7a6b-703c-8060-8008-70741d401776",
    "TextField": "9b1e7a6b-703c-8060-8008-70744c2d6556",
    "AppShell": "9b1e7a6b-703c-8060-8008-7075be5192e9",
    "Drawer": "9b1e7a6b-703c-8060-8008-70750e4567f4",
    "Dialog / md": "9b1e7a6b-703c-8060-8008-7074ad832f95",
}
CONFLICT_SIBLINGS = {
    "复制本地草稿 action": ("1099f839-63e4-80b7-8008-9095d05fe01e", (64, 11460, 140, 44)),
    "重新加载服务器版本 action": ("1099f839-63e4-80b7-8008-9095d05fe01c", (212, 11460, 140, 44)),
    "本地草稿保留说明": ("1099f839-63e4-80b7-8008-9095d0effbbf", (40, 11520, 310, 30)),
}


def _png_size(path: Path) -> tuple[int, int]:
    payload = path.read_bytes()
    assert payload[:8] == b"\x89PNG\r\n\x1a\n"
    return unpack(">II", payload[16:24])


def _notes_section() -> str:
    handoff = HANDOFF.read_text("utf-8")
    return handoff.split("## Learning Notes vertical slice", 1)[1].split("\n## ", 1)[0]


def _parse_board_rows(section: str) -> dict[str, tuple[str, str, tuple[int, int], str, str]]:
    rows = {}
    for line in section.splitlines():
        if not line.startswith("| `") or " / Notes / " not in line:
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        board = cells[0].strip("`")
        page = re.fullmatch(r"`([^`]+)` / `([0-9a-f-]+)`", cells[1])
        viewport = re.fullmatch(r"(\d+) × (\d+)", cells[2])
        board_id = re.fullmatch(r"`([0-9a-f-]+)`", cells[3])
        export = re.fullmatch(r"\[`([^`]+)`\]\(reference/penpot/([^)]+)\)", cells[4])
        assert page and viewport and board_id and export, line
        assert export.group(1) == export.group(2), line
        rows[board] = (
            page.group(1), page.group(2),
            (int(viewport.group(1)), int(viewport.group(2))),
            board_id.group(1), export.group(1),
        )
    return rows


def test_notes_penpot_exports_are_exact_and_have_expected_viewports():
    expected_exports = {record[4] for record in EXPECTED_BOARDS.values()}
    actual_exports = {path.name for path in REFERENCE.glob("*-notes*.png")}
    assert actual_exports == expected_exports
    for record in EXPECTED_BOARDS.values():
        assert _png_size(REFERENCE / record[4]) == record[2]


def test_notes_handoff_binds_final_revision_boards_and_exports():
    section = _notes_section()
    revision = re.search(r"final saved source revision `(\d+)`", section)
    assert revision and int(revision.group(1)) == FINAL_REVISION
    assert _parse_board_rows(section) == EXPECTED_BOARDS
    assert "Notes boards use illustrative sample data only" in section


def test_notes_handoff_binds_shared_ids_and_conflict_intersection_audit():
    section = _notes_section()
    shared_rows = {
        name.strip(): penpot_id
        for name, penpot_id in re.findall(
            r"^\|\s*`([^`]+)`\s*\|\s*`([0-9a-f-]+)`\s*\|\s*$", section, re.MULTILINE,
        )
        if name.strip() in SHARED_COMPONENTS
    }
    assert shared_rows == SHARED_COMPONENTS

    sibling_rows = {}
    for name, penpot_id, x, y, width, height in re.findall(
        r"^\|\s*`([^`]+)`\s*\|\s*`([0-9a-f-]+)`\s*\|\s*`x=(\d+), y=(\d+), w=(\d+), h=(\d+)`\s*\|\s*$",
        section,
        re.MULTILINE,
    ):
        if name in CONFLICT_SIBLINGS:
            sibling_rows[name] = (penpot_id, (int(x), int(y), int(width), int(height)))
    assert sibling_rows == CONFLICT_SIBLINGS

    rectangles = [record[1] for record in sibling_rows.values()]

    def intersects(first: tuple[int, int, int, int], second: tuple[int, int, int, int]) -> bool:
        first_x, first_y, first_width, first_height = first
        second_x, second_y, second_width, second_height = second
        return (
            first_x < second_x + second_width
            and first_x + first_width > second_x
            and first_y < second_y + second_height
            and first_y + first_height > second_y
        )

    pairs = list(combinations(rectangles, 2))
    intersection_count = sum(intersects(first, second) for first, second in pairs)
    assert len(pairs) == 3
    assert intersection_count == 0

    action_names = ("复制本地草稿 action", "重新加载服务器版本 action")
    action_row_bottom = max(
        sibling_rows[name][1][1] + sibling_rows[name][1][3] for name in action_names
    )
    preservation_top = sibling_rows["本地草稿保留说明"][1][1]
    computed_gap = preservation_top - action_row_bottom
    assert computed_gap == 16

    audit = re.search(
        r"all `(\d+)` unique sibling pairs.*?`(\d+)` intersections.*?minimum vertical gap is `(\d+) px`",
        section,
    )
    assert audit and tuple(map(int, audit.groups())) == (
        len(pairs), intersection_count, computed_gap,
    )
