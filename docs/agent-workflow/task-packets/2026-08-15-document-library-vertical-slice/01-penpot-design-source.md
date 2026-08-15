---
id: "document-library-vertical-slice-01"
title: "Create the verified Penpot document-library source"
status: "blocked"
parallel-safe: true
depends-on: []
base-commit: "f90883e71d2fa73a7cb981b11478b68519d8ce80"
owner: "unassigned"
---

# Task Packet: Create the verified Penpot document-library source

## Goal

Extend the existing 知研 Penpot file with the approved document-library desktop, tablet, mobile and state boards, then commit direct exports and an ID-complete handoff that later React work can implement without guessing.

## Non-goals

- Do not write React, API, import, RAG, Memory or authentication code.
- Do not create a new Penpot/Figma file, redesign the AppShell, or alter existing login/AppShell/session boards.
- Do not add component-map entries before corresponding code components exist.

## Delivery context

Penpot is the sole UI authority. The current file already contains Tokens, 20 component families and seven pages, but no document-library screens. The approved direction is list-first, with complete desktop/tablet/mobile boards plus empty, importing, partial-failure and mobile import-sheet states. Production may not fabricate the illustrative records used to compose design boards.

## Relevant files and current interfaces

- `docs/product-ui/penpot-handoff.md` — file ID `3be9e5e1-190f-8090-8008-6ff3f3dcd54c`, seven page IDs, component IDs, responsive rules and existing references.
- `design/tokens/zhiyan.tokens.json` — approved 32-token snapshot; read-only in this packet.
- `docs/product-ui/reference/penpot/*.png` — existing direct exports establish naming and QA practice.
- `tests/deploy/test_product_ui_readme.py` — repository documentation-contract test style.
- Existing changes to preserve: uncommitted plan/review/other packet files under this feature; do not stage them.

## Prerequisites

### Packet dependencies

- none.

### Repository/base state

- Base commit: `f90883e71d2fa73a7cb981b11478b68519d8ce80`.
- Active Penpot file must fresh-read as `3be9e5e1-190f-8090-8008-6ff3f3dcd54c` with the seven handoff page IDs.

### External prerequisites

- User-authenticated Penpot tab with MCP plugin connected and the target file open.

## Explicit change boundary

### Allowed files

- Modify: Penpot file `3be9e5e1-190f-8090-8008-6ff3f3dcd54c` only on the existing Components/Desktop/Tablet/Mobile/States/Handoff pages.
- Modify: `docs/product-ui/penpot-handoff.md`
- Create: `docs/product-ui/reference/penpot/desktop-documents.png`
- Create: `docs/product-ui/reference/penpot/tablet-documents.png`
- Create: `docs/product-ui/reference/penpot/mobile-documents.png`
- Create: `docs/product-ui/reference/penpot/documents-empty.png`
- Create: `docs/product-ui/reference/penpot/documents-importing.png`
- Create: `docs/product-ui/reference/penpot/documents-partial-failure.png`
- Create: `docs/product-ui/reference/penpot/mobile-import-sheet.png`
- Create: `tests/deploy/test_document_library_contract.py`
- Modify: this packet to append its implementation handoff.

### Allowed behavior changes

- Add document-library boards and, only where repeated structure warrants it, DocumentRow/ImportTaskRow/FilePicker library components with token-bound variants.
- Add repository evidence describing those new nodes and exports.

### Forbidden changes

- Do not edit any production Python/TypeScript/CSS, token JSON, component map/schema, dependency manifest, E2E baseline or existing Penpot reference PNG.
- Do not delete/reparent existing library components or reference boards.
- Do not expose a Penpot plugin URL containing a token or any credential.
- Do not stage other uncommitted plan/packet artifacts.

## Interface contract

### Consumes

- Existing AppShell, Button, IconButton, Dialog, Drawer, TextField, Badge, EmptyState and Skeleton IDs from `penpot-handoff.md`.
- Desktop 1440×1024, tablet 1024×768, mobile 390×844 and minimum mobile target 44×44.

### Produces

- Boards named exactly: `Desktop / Documents / Complete`, `Tablet / Documents / Complete`, `Mobile / Documents / Complete`, `State / Documents / Empty`, `State / Documents / Importing`, `State / Documents / Partial failure`, `Mobile / Documents / Import sheet`.
- Fresh-read IDs, component links/variants and seven exact PNG paths recorded in handoff.
- `tests/deploy/test_document_library_contract.py` asserting handoff names, exact PNG set and decoded dimensions.

### Invariants

- Existing board IDs, library links, Tokens and reference exports remain unchanged.
- Ordinary small text keeps WCAG AA contrast; state meaning is not color-only.
- Design examples remain explicitly non-production.

## Required behavior

- Build the approved list-first hierarchy and four key states without invented dashboard/stat cards.
- Desktop/tablet import uses a dialog; mobile uses a bottom sheet; controls remain keyboard-readable and >=44px on mobile.
- Fresh-read every created board after writing; broken links, text overflow and actual-bounds overflow are all zero.
- Direct exports must be original board size, non-empty, decoded and visually inspected.

## Implementation guidance

Follow Task 1 in the source plan exactly. Write the failing contract first. Before every Penpot write assert active file/page/parent IDs. Reuse existing components and Tokens. If the connector rejects a write or node identity differs, stop; never rebuild or guess IDs. Record final file revision and node evidence in handoff.

## Acceptance criteria

- [ ] Seven named boards exist in the correct pages with exact viewport dimensions.
- [ ] All new repeated copies are linked; broken link, text overflow and actual-bounds overflow counts are zero.
- [ ] Every mobile interactive target is at least 44×44.
- [ ] Seven direct PNGs decode to the expected dimensions and pass visual inspection.
- [ ] Handoff names all IDs, exports, states, responsive rules and non-production sample boundary.
- [ ] Focused contract and `git diff --check` pass.

## Test and verification commands

Run from repository root:

```powershell
New-Item -ItemType Directory -Force .runtime | Out-Null
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q tests/deploy/test_document_library_contract.py --basetemp=.runtime/pytest-penpot-documents
git diff --check
```

Expected: all contract tests PASS; seven expected PNGs only, exact dimensions, no diff errors.

## Stop conditions

Stop and report `blocked` if any standard reality-conflict condition in `docs/agent-workflow/README.md` occurs, especially: connector unavailable; active file/page/component/parent mismatch; existing design node would need deletion/reparent; an allowed-file boundary is insufficient; another worker is writing the same Penpot file.

## Implementation handoff

- Packet: `document-library-vertical-slice-01`
- Status: `blocked`
- Delivered:
  - Verified the repository prerequisite state and stopped before implementation because this session exposes no Penpot MCP/connector capable of fresh-reading or writing the required file.
- Files changed:
  - `docs/agent-workflow/task-packets/2026-08-15-document-library-vertical-slice/01-penpot-design-source.md` — records the blocked handoff and reality conflict.
- Interfaces added or changed:
  - `none`
- Acceptance evidence:
  - [ ] Seven named boards exist — not attempted because the Penpot connector prerequisite is unavailable.
  - [ ] Direct PNG exports and ID-complete handoff exist — not attempted because board identity cannot be verified or safely written.
  - [ ] Focused contract passes — no contract was written because TDD implementation may not begin past the external-prerequisite stop condition.
- Verification:
  - `git rev-parse HEAD` — PASS (`7ae7314d09acfddf91653db644649fa0b55add9d`, the planning commit specified by the assignment)
  - `git status --short` — PASS (clean before this blocked-report update)
  - `ALL_TOOLS.filter(tool => /penpot/i.test(tool.name))` — FAIL prerequisite (zero Penpot tools exposed)
  - `list_mcp_resources({})` and `list_mcp_resource_templates({})` — FAIL prerequisite (no Penpot server/resource; no resource templates)
- Scope confirmation:
  - changed only allowed files: yes
  - forbidden areas untouched: yes
- Deviations:
  - `none`; implementation stopped exactly at the connector-unavailable stop condition.
- Residual risks/follow-ups:
  - Connect an authenticated Penpot MCP session with file `3be9e5e1-190f-8090-8008-6ff3f3dcd54c` open, then return this packet to `ready` only after the file and all seven page IDs can be fresh-read.
- Commit:
  - `not committed`

## Reality-conflict report

- Packet: `document-library-vertical-slice-01`
- Status: blocked
- Expected by packet:
  - A user-authenticated Penpot tab with an MCP plugin connected and target file `3be9e5e1-190f-8090-8008-6ff3f3dcd54c` open.
  - A connector that can fresh-read the active file, the seven recorded page IDs, component IDs and parent IDs before every write.
- Observed in repository:
  - `docs/product-ui/penpot-handoff.md:8-24` records the expected file ID and all seven expected page IDs.
  - Current tool discovery returned zero tool names containing `penpot`.
  - Current MCP discovery returned no Penpot server or resource, and no MCP resource templates.
  - The available recommended-plugin list contains no Penpot plugin, so no approved install path is available in this session.
- Impact:
  - The active file/page/component/parent identities cannot be asserted. Continuing would require guessing IDs or substituting a non-Penpot authoring path, both explicitly forbidden by this packet.
- Work completed before pause:
  - Read `PROJECT_KNOWLEDGE.md`, the assigned packet, workflow protocol and task-packet template.
  - Verified HEAD `7ae7314d09acfddf91653db644649fa0b55add9d` and a clean initial worktree.
  - No Penpot nodes, handoff documentation, tests or PNG exports were created or modified.
- Recommended resolution:
  - revise packet
- Decision required:
  - Expose/connect the Penpot MCP connector in this Codex session with the authenticated target file open, then confirm the packet may be returned to `ready` and resumed.
