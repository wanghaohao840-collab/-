---
id: "notes-vertical-slice-01"
title: "建立 Notes Penpot 权威设计源"
status: "done"
parallel-safe: true
depends-on: []
base-commit: "8ac2775dc2cb0563095f0fad5b4a83abcbf52fb9"
owner: "unassigned"
---

# Task Packet: 建立 Notes Penpot 权威设计源

## Goal

在现有“知研 · 智能文档学习助手”Penpot 文件中建立并 fresh-read 十五个 Notes 权威画板，直接导出精确尺寸 PNG，并用仓库设计契约绑定真实 board/component ID。

## Non-goals

- 不修改 React、Python、API 或数据库代码。
- 不用 HTML/浏览器截图冒充 Penpot 源，不重做现有全局品牌或共享组件系统。
- 不实现文件夹、协作、离线编辑或其他路线页面。

## Delivery context

Penpot 是本项目唯一视觉源。现有文档库与 QA handoff 已建立 fresh-read、直接导出、链接组件、bounds 和 44px 审计格式；Notes 必须沿用同一证据标准，覆盖 desktop/tablet/mobile 与关键失败/空/弹层状态。

## Relevant files and current interfaces

- `docs/product-ui/penpot-handoff.md:178-221` — QA 的 board 表、fresh-read、export 和实现绑定写法。
- `docs/product-ui/penpot-component-map.json` — 现有 React↔Penpot ID 映射；必须保持 schema。
- `tests/design/test_penpot_component_map.mjs:129-152` — 必需组件与 freshness 验证。
- `tests/design/test_penpot_handoff.mjs:79` — PNG 和 handoff 结构验证模式。
- `docs/superpowers/specs/2026-08-30-notes-vertical-slice-design.md:300-355` — 已批准三档布局、状态和 Penpot 交接要求。
- Existing changes to preserve: 本目录中的 `REVIEW.md` 与其他 numbered packets。

## Prerequisites

### Packet dependencies

- none.

### Repository/base state

- Base commit: `8ac2775dc2cb0563095f0fad5b4a83abcbf52fb9`.
- Existing semantic tokens and linked AppShell/Button/TextField/Drawer/Dialog masters remain authoritative.

### External prerequisites

- Authenticated access to Penpot file `3be9e5e1-190f-8090-8008-6ff3f3dcd54c`.
- Connected Penpot MCP/plugin capable of fresh read, write, bounds audit and direct PNG export.

## Explicit change boundary

### Allowed files

- Modify: `docs/agent-workflow/task-packets/2026-08-30-notes-vertical-slice/01-penpot-notes-source.md` (status and handoff only)
- Modify: `docs/product-ui/penpot-handoff.md`
- Modify: `docs/product-ui/penpot-component-map.json`
- Create: `docs/product-ui/reference/penpot/desktop-notes*.png`
- Create: `docs/product-ui/reference/penpot/tablet-notes*.png`
- Create: `docs/product-ui/reference/penpot/mobile-notes*.png`
- Create: `tests/deploy/test_notes_product_contract.py`
- Modify: `tests/design/test_penpot_component_map.mjs`

### Allowed behavior changes

- Add Notes-only design boards, exports, mapping records and contract assertions.
- Link existing component masters and add Notes-specific components only where no existing primitive fits.

### Forbidden changes

- No files under `app/`, `api/`, `assistants/`, `ui/` or `web/src/`.
- Do not change existing board/component IDs, token values or exported QA/document PNGs.
- Do not use sample data as a production seed or claim visual proof without fresh-read evidence.
- Do not place credentials, plugin secrets or personal data in repository artifacts.

## Interface contract

### Consumes

- Approved Notes design spec and the existing Penpot file/component library.
- Exact viewports: desktop `1440×1024`, tablet `1024×768`, mobile `390×844`.

### Produces

- Fifteen boards/exports named in Task 1 of `docs/superpowers/plans/2026-08-30-notes-vertical-slice.md`.
- Final saved revision, page/board IDs, linked component IDs, states, tokens and deliberate differences in handoff/map.

### Invariants

- Zero broken links and visible/actual bounds overflow.
- Mobile interactive controls are at least `44×44` CSS px.
- Every visible record/value is labeled illustrative sample data only.

## Required behavior

- Desktop: default, source deleted, projection failed, clear confirm, empty.
- Tablet: default, sources drawer, projection failed, empty.
- Mobile: list, editor, filters drawer, sources drawer, version conflict, empty.
- Desktop is list/editor/source; tablet is list/editor + focus-trapped source Drawer; mobile is list→editor with bottom drawers/dialogs.
- Source tombstone shows only “来源已删除”; conflict preserves local draft choices; projection failure is non-blocking.
- Export directly from final boards, decode/inspect each PNG at original size, then fresh-read final revision and IDs.

## Implementation guidance

1. Write and run the failing Notes product contract before creating exports.
2. Reuse linked masters/tokens; avoid detached copies.
3. Audit text bounds, actual bounds, overlay order and mobile targets on every state main.
4. Direct-export to exact repository filenames, inspect at original size, then update handoff/map with final revision evidence.
5. Run both design tests and `git diff --check`.

## Acceptance criteria

- [ ] Fifteen exact-dimension PNGs decode and match their named viewports.
- [ ] Handoff contains final revision, page/board/component IDs, sample-data boundary and zero-overflow/link evidence.
- [ ] Component map validates against schema and records freshly verified IDs.
- [ ] Mobile audits report no interactive target below 44×44.
- [ ] No non-design file changed.

## Test and verification commands

Run from repository root:

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/deploy/test_notes_product_contract.py --basetemp=.runtime/pytest-notes-penpot
node --test tests/design/test_penpot_handoff.mjs tests/design/test_penpot_component_map.mjs
git diff --check
```

Expected: all commands PASS; PNGs visually inspected at original size.

## Stop conditions

Stop and report `blocked` if Penpot cannot be fresh-read/direct-exported, the file/revision differs, a required shared master is missing, or any change outside allowed files is required. Use the reality-conflict format in `docs/agent-workflow/README.md`.

## Reality-conflict report

- Packet: `notes-vertical-slice-01`
- Status: blocked
- Expected by packet:
  - A connected Penpot MCP/plugin exposing callable fresh-read, write, bounds-audit and direct-PNG-export operations for file `3be9e5e1-190f-8090-8008-6ff3f3dcd54c`.
- Observed in repository/runtime:
  - The authenticated Penpot file opened successfully and its UI reported `MCP connected`.
  - The page WebMCP capability returned `No WebMCP tools are available in this document.`
  - Dynamic callable-tool discovery returned no Penpot, fresh-read, board-export or design-file tools, so the connected bridge cannot be invoked from this task.
  - The required RED command failed with `2 failed`: all 15 Notes exports are absent and the Notes handoff board records are absent.
- Impact:
  - The packet requires authoritative Penpot writes, fresh readback, link/bounds/44px audits and direct exports. Without callable Penpot operations, continuing would require fabricating board IDs, revision evidence or PNGs and would violate the packet stop conditions.
- Work completed before pause:
  - Changed this packet from `ready` to `in_progress`, then to `blocked`.
  - Added `tests/deploy/test_notes_product_contract.py` exactly as the failing design contract.
  - Ran the RED contract and recorded the expected two failures.
  - Did not modify `docs/product-ui/penpot-handoff.md`, `docs/product-ui/penpot-component-map.json`, design-map tests or any product code; no PNG was created.
- Recommended resolution:
  - Revise the execution prerequisite by exposing the connected Penpot MCP server's callable tools to this Codex task (fresh read, write, bounds audit and direct export), then resume this packet from the RED state.
- Decision required:
  - Can the Penpot MCP tool surface be attached to this task so Packet 01 can be resumed without changing its acceptance criteria?

### Resolution

- Resolved on 2026-08-31: the official local `@penpot/mcp@stable` 2.15.4 service is reachable at `http://127.0.0.1:4401/mcp`, with the Penpot MCP Plugin kept open and reporting Connected.
- The bridge was validated with MCP `initialize`, `notifications/initialized`, and `tools/call execute_code`; `penpotUtils.getPages()` returned the expected seven pages.
- Packet 01 resumed from its existing RED contract without weakening acceptance criteria.

## Implementation handoff

- Status: done
- Files changed:
  - `docs/agent-workflow/task-packets/2026-08-30-notes-vertical-slice/01-penpot-notes-source.md`
  - `docs/product-ui/penpot-handoff.md`
  - `docs/product-ui/reference/penpot/desktop-notes.png`
  - `docs/product-ui/reference/penpot/desktop-notes-source-deleted.png`
  - `docs/product-ui/reference/penpot/desktop-notes-projection-failed.png`
  - `docs/product-ui/reference/penpot/desktop-notes-clear.png`
  - `docs/product-ui/reference/penpot/desktop-notes-empty.png`
  - `docs/product-ui/reference/penpot/tablet-notes.png`
  - `docs/product-ui/reference/penpot/tablet-notes-sources.png`
  - `docs/product-ui/reference/penpot/tablet-notes-projection-failed.png`
  - `docs/product-ui/reference/penpot/tablet-notes-empty.png`
  - `docs/product-ui/reference/penpot/mobile-notes.png`
  - `docs/product-ui/reference/penpot/mobile-notes-editor.png`
  - `docs/product-ui/reference/penpot/mobile-notes-filters.png`
  - `docs/product-ui/reference/penpot/mobile-notes-sources.png`
  - `docs/product-ui/reference/penpot/mobile-notes-conflict.png`
  - `docs/product-ui/reference/penpot/mobile-notes-empty.png`
  - `tests/deploy/test_notes_product_contract.py`
  - `tests/design/test_penpot_component_map.mjs`
- Acceptance criteria:
  - [x] Fifteen exact-dimension PNGs decode and match their named viewports.
  - [x] Handoff contains final revision `152`, exact page/board/shared-component IDs, sample-data boundary and zero-overflow/link evidence.
  - [x] Component map validates against schema; the Notes design test pins the freshly read Button, TextField, AppShell, Drawer and Dialog IDs already recorded in the map.
  - [x] Mobile audits report 6/5/11/7/7/5 named actions with zero interactive targets below 44×44.
  - [x] The conflict-state preservation message is separated from both 44 px actions by 16 px; all three foreground sibling pairs have zero intersections.
  - [x] No product-code or other packet file changed; the pre-existing `.superpowers/sdd/progress.md` worktree change was preserved and excluded.
- Verification:
  - `& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/deploy/test_notes_product_contract.py --basetemp=.runtime/pytest-notes-penpot-red` — EXPECTED FAIL (`2 failed`; missing exports and handoff records).
  - `& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/deploy/test_notes_product_contract.py --basetemp=.runtime/pytest-notes-penpot` — PASS (`2 passed`) for the original delivery.
  - `& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/deploy/test_notes_product_contract.py --basetemp=.runtime/pytest-notes-penpot-corrective-red` — EXPECTED FAIL (`2 failed, 1 passed`; revision/shared-ID/audit evidence not yet recorded).
  - `& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/deploy/test_notes_product_contract.py --basetemp=.runtime/pytest-notes-penpot-corrective-green` — PASS (`3 passed`).
  - `node --test tests/design/test_penpot_handoff.mjs tests/design/test_penpot_component_map.mjs` — PASS (`10 passed`).
  - `git diff --check` — PASS (silent except existing line-ending notices).
  - Penpot final fresh read — PASS at revision `152`: file/page IDs unchanged; conflict board `1099f839-63e4-80b7-8008-9095ca611a4f` remains `390×844`; action bounds are `(64,11460,140,44)` and `(212,11460,140,44)`; preservation bounds are `(40,11520,310,30)` with actual text bounds contained.
  - Direct export/original-size inspection — PASS: only `mobile-notes-conflict.png` was re-exported from revision `152`; it decodes at `390×844` and visibly preserves both actions and the 16 px separation. The other fourteen direct exports were unchanged.
- Deviations:
  - The original callable-tool reality conflict was resolved through the official local `@penpot/mcp@stable` 2.15.4 HTTP endpoint while the connected plugin remained open; acceptance criteria were not weakened.
  - No Notes-specific component-map entry was added because Packet 01 may not create product code and all Notes boards use existing shared components. The existing map IDs were fresh-read and pinned by the new design test.
- Residual risks:
  - None for this design-source packet. Later runtime packets must use authenticated records and must not copy illustrative values from these boards into seed/fallback data.
- Commit:
  - Initial delivery: `17abfd8` (`design: add learning notes source boards`).
  - Corrective delivery: this new corrective commit; its exact hash is returned in the final handoff response after commit creation.
