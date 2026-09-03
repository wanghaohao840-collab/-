---
id: "product-ui-closure-02"
title: "Clean Penpot Login source and references"
status: "done"
parallel-safe: false
depends-on: ["product-ui-closure-01"]
base-commit: "ef93550f6b0616815314baf2f62263b43536a17e"
owner: "Codex"
---

# Task Packet: Clean Penpot Login source and references

## Goal

The live Penpot source no longer contains the unused blank Foundations board or any Login remember row; TextField and PasswordField internals fill their form width without fixed 400 px component sizing; desktop/tablet/mobile Login boards are verified and exported as authoritative repository PNGs.

## Non-goals

- Do not redesign brand, typography, colors, Register content, AppShell, session states, or backend behavior.
- Do not detach component copies, rewrite component variants, or change token values.
- Do not create browser snapshots or edit React code.

## Delivery context

Penpot is the only design source. The final design review found a zero-child `100×100` board on Foundations and internal field layers that remained 320 px inside a 400 px form. Product strategy A also removes the now-misleading remember row at all three Login viewports. Live connector state is mutable, so exact internal IDs must be read and semantically asserted immediately before writes rather than guessed from stale documents.

## Relevant files and current interfaces

- Penpot file `知研 · 智能文档学习助手`, ID `3be9e5e1-190f-8090-8008-6ff3f3dcd54c`.
- `00 Foundations`, ID `3be9e5e1-190f-8090-8008-6ff3f3dcd54d`.
- `01 Components`, ID `9b1e7a6b-703c-8060-8008-7071c343b8c2`.
- `02 Desktop`, ID `9b1e7a6b-703c-8060-8008-7071c3463d87`.
- `03 Tablet`, ID `9b1e7a6b-703c-8060-8008-7071c9876902`.
- `04 Mobile`, ID `9b1e7a6b-703c-8060-8008-7071c9888df9`.
- Blank board `0f745b42-1a51-801c-8008-6ff39f5b8841`, previously verified as 100×100 with zero children.
- TextField main `9b1e7a6b-703c-8060-8008-70743ee69ea9`.
- PasswordField main `9b1e7a6b-703c-8060-8008-7074e2350798`.
- Desktop Login `9b1e7a6b-703c-8060-8008-70761a57accd`, 1440×1024.
- Mobile Login `9b1e7a6b-703c-8060-8008-707701065fab`, 390×844; Hero 188 px and Form 656 px.
- `docs/product-ui/penpot-handoff.md:59-75` — current board table and obsolete remember `/legacy` wording.
- `docs/product-ui/reference/penpot/desktop-login.png` and `mobile-login.png` — current real exports.
- Existing changes to preserve: completed packet 01 commit and workflow files.

## Prerequisites

### Packet dependencies

- `product-ui-closure-01` must be `done`.

### Repository/base state

- The worktree must be clean after packet 01 except packet status/handoff edits.
- Existing six Penpot reference PNGs must decode at their documented dimensions.

### External prerequisites

- Penpot plugin connected to the exact target file in the user's signed-in session.
- Active file/page must be readable before every mutation batch.

## Explicit change boundary

### Allowed files/state

- External modify: the exact Penpot file above.
- Create: `tests/design/test_penpot_handoff.mjs`
- Modify: `docs/product-ui/penpot-handoff.md`
- Modify: `docs/product-ui/README.md`
- Replace: `docs/product-ui/reference/penpot/desktop-login.png`
- Create: `docs/product-ui/reference/penpot/tablet-login.png`
- Replace: `docs/product-ui/reference/penpot/mobile-login.png`

### Allowed behavior changes

- Delete the one verified empty board.
- Set exact component child horizontal sizing to fill.
- Remove exact Login remember rows and reflow their forms.

### Forbidden changes

- Do not edit code, tokens, component maps/schema, non-Login reference PNGs, Register semantics, app shell/state boards, or any other Penpot file.
- Do not guess IDs, recreate existing components, detach copies, or hard-code 400 px widths.
- Do not write if file/page/parent/shape assertions fail or connector calls time out.

## Interface contract

### Consumes

- Live Penpot shape tree and existing component links.
- Approved viewport contracts: desktop 1440×1024, tablet 1024×768, mobile 390×844.

### Produces

- Live removal/fill/readback evidence with exact internal IDs.
- Three real Penpot Login exports and a handoff contract binding them to boards/dimensions.

### Invariants

- All Login/Register field copies remain linked.
- Password eye remains fixed 44×44 and right-aligned.
- Register boards remain complete; component/token architecture is unchanged.
- No board/text/actual-bounds overflow.

## Required behavior

1. Fresh-read file/page/revision and every target immediately before writes.
2. Discover exact Tablet Login, remember-row, TextField Input, PasswordField Input/spacer/eye IDs; require expected semantic names, parents, sizes, and component ancestry.
3. Delete only the empty board; update only the exact component children; remove only the three Login remember rows.
4. Validate desktop input/button edges `x=900..1300`, input 44 px, submit 48 px, eye 44×44, Mobile Hero/Form geometry, linkage and zero overflow.
5. Export original-size desktop/tablet/mobile Login PNGs through live `export_shape`, visually inspect, then document exact evidence.

## Implementation guidance

- Use `penpotUtils.getPageByName`, `findShapeById`, `findShapes`, `shapeStructure`, and `analyzeDescendants`; do not reimplement global traversal.
- Limit each connector call to one active page or a few known IDs to stay below the 30-second ceiling.
- Store successful discovery results in plugin `storage` and revalidate them before mutation.
- For flex layouts, mutate `layoutChild.horizontalSizing = "fill"`; do not resize read-only width directly or fight the parent layout.
- After deleting remember rows, adjust flex gap/padding only if necessary; never position linked controls by absolute offsets.
- Export only after fresh-read verification, and open every written PNG.

## Acceptance criteria

- [x] Blank board ID is absent and the Foundation System board remains.
- [x] TextField Input and PasswordField Input/spacer read back `horizontalSizing=fill`; eye is fixed 44×44/right-aligned.
- [x] Desktop/tablet/mobile Login contain no “保持登录状态” text/checkbox/row; Register content remains complete.
- [x] All affected instances are linked with zero text/actual-bounds overflow.
- [x] Three real Penpot exports exist at exact dimensions and pass visual inspection.
- [x] Handoff contract, component-map test, token freshness, secret/Figma scan, and diff check pass.

## Test and verification commands

Run from repository root after live Penpot validation:

```powershell
node --test tests/design/test_penpot_handoff.mjs tests/design/test_penpot_component_map.mjs
node scripts/design_tokens.mjs --check design/tokens/zhiyan.tokens.json web/src/styles/tokens.css
rg -n "userToken|figma.com|Figma Code Connect" design docs/product-ui
git diff --check
```

Expected: Node tests/token check/diff exit 0; the secret/Figma search returns zero matches (rg exit 1 is the expected no-match result). Manual step: open all three repository PNGs and verify exact dimensions, no clipping, aligned field/button edges, and absence of remember rows.

## Stop conditions

Stop and append a reality-conflict report if the plugin is disconnected/times out repeatedly, a known ID resolves differently, multiple semantic candidates exist, a linked instance requires detachment, Register would lose content, export cannot write a real PNG, or required edits exceed the allowed source/docs/reference paths.

## Implementation handoff

- Status: done
- Commit: `fd6706e` (`fix: clean Penpot login source`)
- Live source: Penpot 2.17.1, file `3be9e5e1-190f-8090-8008-6ff3f3dcd54c`, revision `89`, seven unique pages.
- Removed objects:
  - Foundations blank board `0f745b42-1a51-801c-8008-6ff39f5b8841`.
  - Desktop remember row `9b1e7a6b-703c-8060-8008-70761b2c4b92`.
  - Tablet remember row `9b1e7a6b-703c-8060-8008-7076b6e5a9c5`.
  - Mobile remember row `9b1e7a6b-703c-8060-8008-70770173089e`.
- Responsive source readback:
  - TextField Input `9b1e7a6b-703c-8060-8008-70743ef84e3c`: `fill`.
  - PasswordField Input `9b1e7a6b-703c-8060-8008-7074e250419f`: `fill`.
  - PasswordField Spacer `9b1e7a6b-703c-8060-8008-7074e286ce22`: `fill`.
  - PasswordField Eye `9b1e7a6b-703c-8060-8008-7074e28f276b`: `fix`, `44 × 44`.
- Board verification:
  - Desktop Login `9b1e7a6b-703c-8060-8008-70761a57accd`: `1440 × 1024`, 14 linked copies, zero broken links/overflow, fields and submit aligned at `x=900..1300`.
  - Tablet Login `9b1e7a6b-703c-8060-8008-7076b66de7ca`: `1024 × 768`, 12 linked copies, zero broken links/overflow.
  - Mobile Login `9b1e7a6b-703c-8060-8008-707701065fab`: `390 × 844`, 12 linked copies, zero broken links/overflow; Hero/Form remain `188/656` px.
  - Desktop/Tablet/Mobile Register boards retain ConfirmPassword and their original controls.
- Export evidence:
  - `desktop-login.png`: 80,990 bytes, SHA-256 `d632431cea0ce30c8f22bc96aefc63bcfe5157b3483fdadd1f3863611753f4f2`.
  - `tablet-login.png`: 57,164 bytes, SHA-256 `a8dc0bafb315edfeed6721735360dbb49beecb7bd1a914c325589ee8167857b0`.
  - `mobile-login.png`: 33,596 bytes, SHA-256 `e7744484e3b35c6d710a8df156d397d41b7ecd9badecb5d5100296bc5df0a493`.
  - All three were exported by live `export_shape` at original dimensions and opened for visual inspection; no remember row, clipping, misalignment, or missing glyph was found.
- Repository gates:
  - Handoff/component-map: 9 passed.
  - Token freshness: pass.
  - `git diff --check`: pass.
  - The focused credential/Figma scan returned only two pre-existing defensive literals: the component-map schema rejection pattern and README guidance prohibiting credential copying; no credential-bearing URL or Figma source is present.
- Scope: no React, backend, token, Register, AppShell, or session-state source was changed by this packet.
