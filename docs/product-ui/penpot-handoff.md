# 知研 Penpot product UI handoff

Validated on 2026-08-11 against Penpot 2.17.1 at file revision `89`. Penpot is the sole design source for this product UI; the former design file is an archive only.

The document-library vertical slice was fresh-read on 2026-08-22 against Penpot 2.17.2 at final saved source revision `119`. Direct semantic export calls returned the seven boards during the revision-`118` autosave window; a stable revision-`119` fresh read verified identical board/component identities, states, hierarchy and geometry, so there is no semantic delta between the exported source and final saved source. These revisions add native state variants for the three document-library component families without changing any pre-existing shared master.

## Source file

- File: `知研 · 智能文档学习助手`
- File ID: `3be9e5e1-190f-8090-8008-6ff3f3dcd54c`
- File URL: <https://design.penpot.app/#/workspace?team-id=81f57451-85cc-819d-8008-6f89f7eef6c2&file-id=3be9e5e1-190f-8090-8008-6ff3f3dcd54c>
- Token snapshot: [`design/tokens/zhiyan.tokens.json`](../../design/tokens/zhiyan.tokens.json)

The URL above was read from the active Penpot browser session and reduced to the team and file identifiers. It contains no credential parameter.

## Pages

| Page | Penpot page ID |
|---|---|
| `00 Foundations` | `3be9e5e1-190f-8090-8008-6ff3f3dcd54d` |
| `01 Components` | `9b1e7a6b-703c-8060-8008-7071c343b8c2` |
| `02 Desktop` | `9b1e7a6b-703c-8060-8008-7071c3463d87` |
| `03 Tablet` | `9b1e7a6b-703c-8060-8008-7071c9876902` |
| `04 Mobile` | `9b1e7a6b-703c-8060-8008-7071c9888df9` |
| `05 States` | `9b1e7a6b-703c-8060-8008-7071d0888e76` |
| `06 Handoff` | `9b1e7a6b-703c-8060-8008-7071d08a26f7` |

## Components

The first ID is the library component or native Variant container; the second is its default main shape. All page copies remain linked.

| Component | Library / Variant ID | Default main shape ID |
|---|---|---|
| Button | `9b1e7a6b-703c-8060-8008-70741d401776` | `9b1e7a6b-703c-8060-8008-70740dec187c` |
| IconButton | `9b1e7a6b-703c-8060-8008-7074e20c9b5a` | `9b1e7a6b-703c-8060-8008-7074e1d47f84` |
| TextField | `9b1e7a6b-703c-8060-8008-70744c2d6556` | `9b1e7a6b-703c-8060-8008-70743ee69ea9` |
| PasswordField | `9b1e7a6b-703c-8060-8008-7074e298a9f5` | `9b1e7a6b-703c-8060-8008-7074e2350798` |
| Checkbox | `9b1e7a6b-703c-8060-8008-7074e3054e2d` | `9b1e7a6b-703c-8060-8008-7074e2be584e` |
| Badge | `9b1e7a6b-703c-8060-8008-7074e35382d4` | `9b1e7a6b-703c-8060-8008-7074e32d7ba7` |
| Avatar | `9b1e7a6b-703c-8060-8008-7074e3b4ac40` | `9b1e7a6b-703c-8060-8008-7074e379d24b` |
| Tooltip | `9b1e7a6b-703c-8060-8008-7074e4155e4e` | `9b1e7a6b-703c-8060-8008-7074e3dca59b` |
| Toast | `9b1e7a6b-703c-8060-8008-70747f222074` | `9b1e7a6b-703c-8060-8008-707474b7848f` |
| Dialog | `9b1e7a6b-703c-8060-8008-7074b78f6059` | `9b1e7a6b-703c-8060-8008-7074acc46e6a` |
| Drawer | `9b1e7a6b-703c-8060-8008-70750e4567f4` | `9b1e7a6b-703c-8060-8008-70750df2bd93` |
| Tabs | `9b1e7a6b-703c-8060-8008-7074e4a1d726` | `9b1e7a6b-703c-8060-8008-7074e42b4132` |
| SidebarItem | `9b1e7a6b-703c-8060-8008-7074661b3a33` | `9b1e7a6b-703c-8060-8008-70745af904b2` |
| Sidebar | `9b1e7a6b-703c-8060-8008-70750c0c6430` | `9b1e7a6b-703c-8060-8008-70750b4e85e3` |
| MobileBottomNav | `9b1e7a6b-703c-8060-8008-70750d23f8e1` | `9b1e7a6b-703c-8060-8008-70750c27c657` |
| TopBar | `9b1e7a6b-703c-8060-8008-70750d788aa4` | `9b1e7a6b-703c-8060-8008-70750d400b73` |
| PageHeader | `9b1e7a6b-703c-8060-8008-70750dd54ad0` | `9b1e7a6b-703c-8060-8008-70750d948c5b` |
| EmptyState | `9b1e7a6b-703c-8060-8008-707540e5d3c1` | `9b1e7a6b-703c-8060-8008-7075405540df` |
| Skeleton | `9b1e7a6b-703c-8060-8008-7075416bddda` | `9b1e7a6b-703c-8060-8008-70754107e046` |
| AppShell | `9b1e7a6b-703c-8060-8008-7075be5192e9` | `9b1e7a6b-703c-8060-8008-707572a1d2e7` |

Native Variant axes are present for Button (`hierarchy`, `size`, `state`, `icon`), TextField (`state`, `label`, `helper`), SidebarItem (`state`, `collapsed`), Toast (`tone`), Dialog (`size`), and AppShell (`viewport`). Focus states use a visible 2 px ring. Disabled, destructive, warning, and selected states also use text, iconography, or opacity, rather than color alone.

## Responsive field cleanup

- Removed empty Foundations board: `0f745b42-1a51-801c-8008-6ff39f5b8841`. The `Board / Foundation System` board remains the sole top-level Foundations board.
- Login remember rows removed from Desktop, Tablet, and Mobile: `9b1e7a6b-703c-8060-8008-70761b2c4b92`, `9b1e7a6b-703c-8060-8008-7076b6e5a9c5`, and `9b1e7a6b-703c-8060-8008-70770173089e`. Register boards and their existing controls were not changed.
- The affected Login forms reflow with their existing 16 px desktop/tablet and 12 px mobile Flex gaps. Desktop and Tablet submit controls now follow PasswordField directly; Mobile retains its 188 px Hero and 656 px Form geometry.

| Internal layer | Penpot shape ID | Horizontal sizing | Fixed geometry |
|---|---|---|---|
| TextField / Input | `9b1e7a6b-703c-8060-8008-70743ef84e3c` | `fill` | — |
| PasswordField / Input | `9b1e7a6b-703c-8060-8008-7074e250419f` | `fill` | — |
| PasswordField / Spacer | `9b1e7a6b-703c-8060-8008-7074e286ce22` | `fill` | — |
| PasswordField / Eye | `9b1e7a6b-703c-8060-8008-7074e28f276b` | `fix` | `44 × 44` |

Fresh readback found 14 linked component copies on Desktop Login, 12 on Tablet Login, and 12 on Mobile Login, with zero broken links, text-bounds overflow, or actual-bounds overflow. Desktop field and submit edges are aligned at `x=900..1300`; the corresponding Tablet and Mobile controls are also equal-width and aligned. Desktop, Tablet, and Mobile Register boards still include ConfirmPassword and retain their original content.

## Reference boards

| Reference | Viewport | Penpot board ID | Export |
|---|---:|---|---|
| Desktop login | 1440 × 1024 | `9b1e7a6b-703c-8060-8008-70761a57accd` | [`desktop-login.png`](reference/penpot/desktop-login.png) |
| Tablet login | 1024 × 768 | `9b1e7a6b-703c-8060-8008-7076b66de7ca` | [`tablet-login.png`](reference/penpot/tablet-login.png) |
| Desktop AppShell | 1440 × 1024 | `9b1e7a6b-703c-8060-8008-70768eb3fbd8` | [`desktop-shell.png`](reference/penpot/desktop-shell.png) |
| Tablet AppShell | 1024 × 768 | `9b1e7a6b-703c-8060-8008-7076da187916` | [`tablet-shell.png`](reference/penpot/tablet-shell.png) |
| Mobile login | 390 × 844 | `9b1e7a6b-703c-8060-8008-707701065fab` | [`mobile-login.png`](reference/penpot/mobile-login.png) |
| Mobile AppShell | 390 × 844 | `9b1e7a6b-703c-8060-8008-7077227fbcfe` | [`mobile-shell.png`](reference/penpot/mobile-shell.png) |
| Session expired | 1440 × 1024 | `9b1e7a6b-703c-8060-8008-70776404ef6f` | [`session-expired.png`](reference/penpot/session-expired.png) |

The seven exports were generated directly from these boards at their original dimensions and visually checked for clipping, overflow, alignment, contrast, and missing glyphs. The three Login exports were refreshed after the responsive field cleanup and contain no remember control.

## Document-library vertical slice

The following top-level boards are the implementation authority for the document-library slice. The page ID identifies the owning Penpot page; the board ID is the direct-export source.

| Board | Page / page ID | Viewport | Penpot board ID | Direct export |
|---|---|---:|---|---|
| `Desktop / Documents / Complete` | `02 Desktop` / `9b1e7a6b-703c-8060-8008-7071c3463d87` | 1440 × 1024 | `f35db4ee-075c-8075-8008-7c1fd37b7550` | [`desktop-documents.png`](reference/penpot/desktop-documents.png) |
| `Tablet / Documents / Complete` | `03 Tablet` / `9b1e7a6b-703c-8060-8008-7071c9876902` | 1024 × 768 | `f35db4ee-075c-8075-8008-7c1ff9755c8f` | [`tablet-documents.png`](reference/penpot/tablet-documents.png) |
| `Mobile / Documents / Complete` | `04 Mobile` / `9b1e7a6b-703c-8060-8008-7071c9888df9` | 390 × 844 | `f35db4ee-075c-8075-8008-7c203fbd929a` | [`mobile-documents.png`](reference/penpot/mobile-documents.png) |
| `State / Documents / Empty` | `05 States` / `9b1e7a6b-703c-8060-8008-7071d0888e76` | 1440 × 1024 | `f35db4ee-075c-8075-8008-7c20be8943e5` | [`documents-empty.png`](reference/penpot/documents-empty.png) |
| `State / Documents / Importing` | `05 States` / `9b1e7a6b-703c-8060-8008-7071d0888e76` | 1440 × 1024 | `f35db4ee-075c-8075-8008-7c210d9ab874` | [`documents-importing.png`](reference/penpot/documents-importing.png) |
| `State / Documents / Partial failure` | `05 States` / `9b1e7a6b-703c-8060-8008-7071d0888e76` | 1440 × 1024 | `f35db4ee-075c-8075-8008-7c214b03b87f` | [`documents-partial-failure.png`](reference/penpot/documents-partial-failure.png) |
| `Mobile / Documents / Import sheet` | `04 Mobile` / `9b1e7a6b-703c-8060-8008-7071c9888df9` | 390 × 844 | `f35db4ee-075c-8075-8008-7c207f34321c` | [`mobile-import-sheet.png`](reference/penpot/mobile-import-sheet.png) |

### Document-library components

These local library components live on `01 Components` (`9b1e7a6b-703c-8060-8008-7071c343b8c2`) under the `DocumentLibrary` path. Each family is a native Penpot Variant container with one `state` axis. The component ID creates the linked state instance; the main shape ID identifies its editable source.

| Family / `state` | Variant container ID | Component ID | Main shape ID | Source size | Bound tokens |
|---|---|---|---|---:|---|
| `DocumentRow / ready` | `879161d4-ba5f-800d-8008-852644bff09e` | `f35db4ee-075c-8075-8008-7c1eea45d28f` | `f35db4ee-075c-8075-8008-7c1ee50626f5` | 1080 × 84 | `color.border`, `color.brand.100`, `color.brand.700`, `color.surface`, `color.text.primary`, `radius.md`, `radius.pill` |
| `DocumentRow / deleting` | `879161d4-ba5f-800d-8008-852644bff09e` | `879161d4-ba5f-800d-8008-852644a6ce86` | `879161d4-ba5f-800d-8008-852643ecd3a3` | 1080 × 84 | ready tokens plus `color.warning` |
| `ImportTaskRow / running` | `879161d4-ba5f-800d-8008-8526d12f81ea` | `f35db4ee-075c-8075-8008-7c1eefe300f4` | `f35db4ee-075c-8075-8008-7c1eea61d96f` | 1080 × 96 | `color.border`, `color.brand.100`, `color.brand.600`, `color.brand.700`, `color.surface`, `color.text.primary`, `radius.md`, `radius.pill`, `space.2` |
| `ImportTaskRow / queued` | `879161d4-ba5f-800d-8008-8526d12f81ea` | `879161d4-ba5f-800d-8008-85267e7cc503` | `879161d4-ba5f-800d-8008-85267cc1920e` | 1080 × 96 | running tokens plus `color.warning` |
| `ImportTaskRow / failed` | `879161d4-ba5f-800d-8008-8526d12f81ea` | `879161d4-ba5f-800d-8008-8526c1b6c14b` | `879161d4-ba5f-800d-8008-85267e911867` | 1080 × 96 | running tokens plus `color.danger` |
| `ImportTaskRow / cancelled` | `879161d4-ba5f-800d-8008-8526d12f81ea` | `879161d4-ba5f-800d-8008-8526c2549491` | `879161d4-ba5f-800d-8008-8526c1c9ac4c` | 1080 × 96 | `color.border`, `color.surface`, `color.text.primary`, `radius.md`, `radius.pill`, `space.2` |
| `FilePicker / idle` | `879161d4-ba5f-800d-8008-8526e7a901fd` | `f35db4ee-075c-8075-8008-7c1ef1831cf3` | `f35db4ee-075c-8075-8008-7c1eeffad986` | 560 × 180 | `color.border`, `color.brand.600`, `color.surface`, `color.text.primary`, `radius.md`, `space.2` |
| `FilePicker / drag-active` | `879161d4-ba5f-800d-8008-8526e7a901fd` | `879161d4-ba5f-800d-8008-8526e71f0357` | `879161d4-ba5f-800d-8008-8526e6bd9d4b` | 560 × 180 | idle tokens; `color.brand.600` binds the active border |
| `FilePicker / invalid` | `879161d4-ba5f-800d-8008-8526e7a901fd` | `879161d4-ba5f-800d-8008-8526e797edf6` | `879161d4-ba5f-800d-8008-8526e731ace8` | 560 × 180 | idle tokens plus `color.danger` for error border/text |

Runtime state maps to Penpot state properties as follows: a usable document uses `DocumentRow state=ready`, while an in-flight delete uses `state=deleting`; import tasks map `queued` and `retry_wait` to `ImportTaskRow state=queued`, `running` to `running`, `failed` to `failed`, and `cancelled` to `cancelled`. A succeeded task leaves the task list and renders as `DocumentRow state=ready`. File selection maps its default/resting state to `FilePicker state=idle`, a valid drag-over to `drag-active`, and rejected type/size/count validation to `invalid`. The Partial failure board's two failed task rows are linked instances of the explicit `failed` variant.

`DocumentRow` is ordered file icon → filename/metadata → textual status badge → 44 × 44 delete action. `ImportTaskRow` is ordered file icon → filename/stage → textual status badge → 44 px-high action. `FilePicker` is ordered title → supported-format/limit copy → 44 px-high browse action. Instances may resize horizontally for their viewport, but this reading order and the linked component identity must remain intact.

### State and data semantics

- Complete: list-first layout with filename filtering, a primary import action, status text and per-row delete actions. Repeated filenames are allowed; `document_id`, not the visible filename, remains the identity. Most-recent import sorts first.
- Empty: show “还没有文档”, “导入 PDF、TXT、Markdown 或 DOCX，开始构建你的知识库。” and the import action. The limit note remains “每批最多 20 个文件 · 单文件 100 MiB · 每批 500 MiB”. Do not replace this state with fabricated counts or statistics.
- Importing: desktop/tablet use the Dialog pattern. File selection creates persistent per-file tasks immediately; progress is announced as processing state, and one file may succeed or fail independently of another. Cancel applies to the addressed task; “继续导入” starts another selection without discarding current progress.
- Partial failure: successful documents remain usable. Each failed task exposes its text reason and a retry action, with a separate “重试全部失败项” action. The summary uses text and iconography as well as color. User-visible failures must not expose local paths, user IDs, credentials, stack traces or raw exceptions.
- Mobile import: use the bottom-sheet board, not a centered dialog. “开始导入” and “取消” are full-width 342 × 44 controls; the close control is 44 × 44. Opening moves focus into the sheet, `Escape` closes it, and focus returns to the “导入文档” trigger.

All filenames, dates, sizes, progress values and task outcomes shown on these boards—including `RAG 系统设计说明.pdf`, `research-notes.md`, `scanned-contract.pdf` and `meeting-notes.docx`—are illustrative design samples only. They are not fixtures, seed records, fallback content, analytics, or permission to fabricate production data. Production renders only records returned for the authenticated user.

### Responsive, interaction and accessibility rules

- Desktop keeps the 248 px sidebar and 64 px top bar. The document content begins at `x=296`; the list panel is 1096 px wide, the name filter is 420 px wide, and the import action is 180 × 44.
- Tablet keeps the 72 px rail and 64 px top bar. The list panel is 856 px wide, the name filter is 360 px wide, and the import action is 160 × 44. Import follows the same modal Dialog semantics as desktop while remaining within the tablet viewport.
- Mobile uses one 342 px content column with the existing 64 px bottom navigation. The filter is 342 × 70, the import action is 342 × 44, and document rows reflow to 310 × 84 inside the list panel. Import uses the 390 × 594 bottom sheet and the 342 × 196 `FilePicker` instance.
- Keyboard order is page heading → import → filename filter → document/task rows and their actions. Use native list semantics for rows, an explicit label for the filename filter, and filename-qualified accessible names for destructive and retry actions.
- File selection supports keyboard activation as well as drag/drop. Announce queued/running/progress/completed/failed/cancelled changes through a polite live region without moving focus. Busy state, failure and success remain understandable without color.
- Every named interactive target on both mobile boards is at least 44 × 44. The bottom navigation targets are 78 × 64; visible focus treatment follows the existing 2 px ring contract.

At final saved source revision `119`, fresh readback found 6/6/6 linked component roots on the desktop/tablet/mobile complete boards, 4 on empty, 7 on importing, 5 on partial failure and 5 on the mobile import sheet, with zero broken component-root links. Visible text-bounds overflow and actual-bounds overflow are zero on all seven boards and every state main in the three new Variant containers. The mobile complete and import-sheet audits found 13 and 10 named interactive targets respectively, with zero below 44 × 44. Component-state readback found ready `DocumentRow` instances on all complete boards, running `ImportTaskRow` and idle `FilePicker` instances on Importing, failed `ImportTaskRow` instances on Partial failure, and an idle `FilePicker` on the mobile sheet. All seven direct semantic PNG exports fully verify and decode at their original board dimensions and passed visual inspection for clipping, alignment, missing glyphs and state clarity. Browser output should match these boards except for the documented font-rendering differences below; no additional document-library deviation is approved.

## Responsive and navigation rules

- Desktop (`≥1200 px`): fixed 248 px sidebar, 64 px top bar, and content capped at 1200 px.
- Tablet (`768–1199 px`): 72 px compact rail and on-demand drawer. Use two columns only while each remains at least 320 px.
- Mobile (`≤767 px`): one content column, 64 px bottom navigation, and controls with a minimum 44 px target. Login omits a persistence control; PasswordField visibility controls remain `44 × 44`, and Mobile AppShell `/legacy/` actions remain `140 × 44`.
- Desktop navigation: 概览、文档库、智能问答、文献检索、学习笔记、学习洞察.
- Mobile navigation: 概览、文档、问答、检索、更多. The 更多 drawer contains 学习笔记、学习洞察、账户、退出登录.
- Unmigrated capability copy: “该能力正在迁移到新版界面，可暂时前往旧版使用。” The secondary action routes to `/legacy/`.

## Keyboard, focus, and accessibility

Keyboard order follows visual reading order: skip link → primary navigation → top-bar actions → page heading → main controls → secondary actions. Drawers move focus to their first actionable element, trap `Tab` and `Shift+Tab` while open, close on `Escape`, and return focus to the trigger. Dialogs follow the same focus trap and return pattern. A single visible page heading is used per route. All ordinary 12–16 px copy and control labels were scanned across the seven pages and bind to `color.text.primary` on the approved light backgrounds; the remaining WCAG AA failure count is zero. The approved `color.text.secondary` value remains `#71847C` but is not used for ordinary small text. Statuses include explicit text and/or icons.

## Typography and rendering notes

Penpot uses Inter for Latin text and the available `Noto Sans SC` face for Chinese samples. Browser implementation must publish this fallback chain:

```css
font-family: Inter, "Noto Sans SC", "PingFang SC", "Microsoft YaHei", sans-serif;
```

The seven Penpot text styles preserve the approved size and line-height pairs. Browser font metrics and antialiasing can differ slightly from the cloud canvas, so visual comparisons should prioritize alignment, wrapping, and hierarchy over subpixel glyph rasterization.

## Deliberate browser differences

- The Desktop, Tablet, and Mobile AppShell reference boards contain illustrative overview metrics, recent-document names, reading progress, and continue-learning content. The Task 4 React routes intentionally do not reproduce those samples: all six product routes render only their real page heading and the approved migration empty state, so the empty-state card occupies the first content slot and the lower canvas remains open. This preserves the no-fabricated-data boundary until the corresponding product slices are implemented.
- The session-expired browser state presents the approved dialog over Login rather than over the authenticated AppShell shown in the Penpot reference. A real `401` immediately unmounts the protected route and its user content before the re-authentication dialog is presented; retaining protected content solely to reproduce the reference backdrop would weaken that security boundary. The dialog geometry, overlay, actions, and focus behavior continue to follow the approved state.
- Navigation blocks use the approved token geometry and state styling without importing an unapproved icon asset set. The visible text labels and `aria-current="page"` remain the authoritative destination and active-state signals.

## Token and implementation boundaries

Penpot tokens are published only through `Penpot → design/tokens/zhiyan.tokens.json → generated CSS`; code must not write values back to Penpot. Penpot 2.17.1 rejects `/` in native token names and does not accept a negative shadow spread, so native effect tokens are named `shadow.surface` and `shadow.overlay`. The Foundations page still presents the approved library labels `Shadow/Surface` and `Shadow/Overlay`; the normalized snapshot records the actual zero-spread native values.

This UI handoff does not alter ApplicationServices, authentication/CSRF behavior, per-user UUID storage, `document_id`, citation, RAG, Memory, reporting, bulk-import, or storage boundaries. The legacy Gradio experience remains available only through `/legacy/`.

## Intelligent QA vertical slice

Validated on 2026-08-26 against Penpot 2.17.2 at final saved source revision `136`. The eight boards below are the implementation authority for the `/qa` product slice. Every visible filename, question, answer, excerpt, date and progress value is illustrative design sample data only; production must render records returned for the authenticated user.

| Board | Page / page ID | Viewport | Penpot board ID | Direct export |
|---|---|---:|---|---|
| `Desktop / QA / Default` | `02 Desktop` / `9b1e7a6b-703c-8060-8008-7071c3463d87` | 1440 × 1024 | `6aef877d-b595-80db-8008-8a8b5dd2bcc1` | [`desktop-qa.png`](reference/penpot/desktop-qa.png) |
| `Desktop / QA / Summary running` | `02 Desktop` / `9b1e7a6b-703c-8060-8008-7071c3463d87` | 1440 × 1024 | `6aef877d-b595-80db-8008-8a8b6c04169b` | [`desktop-qa-summary.png`](reference/penpot/desktop-qa-summary.png) |
| `Desktop / QA / Delete confirm` | `02 Desktop` / `9b1e7a6b-703c-8060-8008-7071c3463d87` | 1440 × 1024 | `6aef877d-b595-80db-8008-8a8b79ed4f86` | [`desktop-qa-delete.png`](reference/penpot/desktop-qa-delete.png) |
| `Tablet / QA / Default` | `03 Tablet` / `9b1e7a6b-703c-8060-8008-7071c9876902` | 1024 × 768 | `6aef877d-b595-80db-8008-8a8bc412e749` | [`tablet-qa.png`](reference/penpot/tablet-qa.png) |
| `Tablet / QA / Sources drawer` | `03 Tablet` / `9b1e7a6b-703c-8060-8008-7071c9876902` | 1024 × 768 | `6aef877d-b595-80db-8008-8a8bd1620cfb` | [`tablet-qa-sources.png`](reference/penpot/tablet-qa-sources.png) |
| `Mobile / QA / Default` | `04 Mobile` / `9b1e7a6b-703c-8060-8008-7071c9888df9` | 390 × 844 | `6aef877d-b595-80db-8008-8a8c1496ce7f` | [`mobile-qa.png`](reference/penpot/mobile-qa.png) |
| `Mobile / QA / Sources sheet` | `04 Mobile` / `9b1e7a6b-703c-8060-8008-7071c9888df9` | 390 × 844 | `6aef877d-b595-80db-8008-8a8c2183678f` | [`mobile-qa-sources.png`](reference/penpot/mobile-qa-sources.png) |
| `Mobile / QA / Failure retry` | `04 Mobile` / `9b1e7a6b-703c-8060-8008-7071c9888df9` | 390 × 844 | `6aef877d-b595-80db-8008-8a8c2ee748a5` | [`mobile-qa-failure.png`](reference/penpot/mobile-qa-failure.png) |

### Linked design-system sources

The QA boards reuse the existing linked library sources rather than detached copies. AppShell uses its native `viewport` variants: desktop `9b1e7a6b-703c-8060-8008-707574c1a629`, tablet `9b1e7a6b-703c-8060-8008-707593dd9adbe`, and mobile `9b1e7a6b-703c-8060-8008-7075b44513a2`. Controls use Button primary `9b1e7a6b-703c-8060-8008-70740e0b1a82`, secondary `9b1e7a6b-703c-8060-8008-70740e41c3da`, ghost `9b1e7a6b-703c-8060-8008-70740e67a9d5`, danger `9b1e7a6b-703c-8060-8008-70740e837ca2`, IconButton `9b1e7a6b-703c-8060-8008-7074e20c9b5a`, Badge `9b1e7a6b-703c-8060-8008-7074e35382d4`, Dialog `size=md` `9b1e7a6b-703c-8060-8008-7074ad832f95`, Drawer `9b1e7a6b-703c-8060-8008-70750e4567f4`, and Skeleton `9b1e7a6b-703c-8060-8008-7075416bddda`.

QA-specific surfaces bind the existing semantic tokens `color.canvas`, `color.surface`, `color.brand.100`, `color.brand.600`, `color.brand.700`, `color.text.primary`, `color.border`, `color.danger`, `radius.md`, `radius.lg`, and `radius.pill`. Small ordinary copy deliberately continues to use `color.text.primary`; status meaning is always paired with text and is never color-only.

### State, responsive and interaction contract

- Desktop keeps the existing 248 px navigation and 64 px top bar. The content region exposes recent conversations, the primary chat and fixed document scope, and current-answer sources at the same time. The composer remains visible in default and summary-running states.
- `Summary running` uses textual stage/progress, a linked Skeleton and an explicit cancel action. It represents durable job polling, not token streaming.
- `Delete confirm` uses a scrim, linked Dialog, explicit Cancel and danger-styled Permanent delete actions. The copy names messages, sources, summary and QA Memory and states that deletion is irreversible.
- Tablet keeps the existing 72 px rail. Conversation and source triggers are 44 px high; the source surface is a mutually exclusive right drawer. Opening moves focus into the drawer, `Escape` closes it, and focus returns to the `引用 2` trigger.
- Mobile keeps one 342 px content column and the existing 64 px bottom navigation. Conversation and source surfaces are bottom sheets ending above that navigation. The source sheet close, copy and return-focus behavior follows the same drawer contract.
- `Failure retry` pairs the danger treatment with the visible `回答失败` label, safe `QA_ENGINE_UNAVAILABLE` code, preserved-message explanation and a linked 44 px-high Retry action. No stack, path, prompt or provider response is shown.
- Citation numbers map the answer chips to immutable source cards. Copy actions provide non-blocking feedback in implementation. The fixed-scope label is not an editable selector; changing document scope creates a new conversation.

### Fresh-read and export evidence

At revision `136`, final page-scoped readback found 8/10/11 linked QA component roots on the three desktop boards, 7/10 on the tablet boards and 6/9/6 on the mobile boards, with zero broken component-root links. The same audit found zero text-bounds overflow and zero actual-bounds overflow on all eight boards. Every named mobile interaction is 44 px or taller/wider as appropriate, with zero mobile targets below 44 × 44.

All eight direct PNG exports fully verify and decode at their original board dimensions and were visually inspected for clipping, overlay order, missing glyphs, fixed-scope clarity, source readability, summary progress, destructive confirmation and retry recovery. No browser-only visual divergence is approved at this stage; packet 06 must compare the React implementation against these exact exports and record any necessary runtime-only difference.

### React runtime acceptance

Packet 07 validated the production React route with a deterministic Python-only answer adapter through the approved `ApplicationServices` test seam; no route interception or production-selectable fake engine exists. Exact browser viewports are Desktop `1440 × 1024`, Tablet `1024 × 768`, and Mobile `390 × 844`. The tracked runtime baselines are `web/e2e/qa.spec.ts-snapshots/qa-default-desktop.png`, `qa-summary-desktop.png`, `qa-delete-desktop.png`, `qa-default-tablet.png`, `qa-sources-tablet.png`, `qa-default-mobile.png`, `qa-sources-mobile.png`, and `qa-failure-mobile.png`.

The runtime uses authenticated, imported records, so filenames, questions and citation counts deliberately differ from the illustrative Penpot samples; geometry, state meaning and responsive behavior remain the comparison contract. Two necessary runtime-only details are recorded: Tablet computes the chat minimum from available viewport height so the heading and complete composer remain visible at `1024 × 768`; Mobile exposes “删除当前对话” inside the existing conversation bottom sheet because deletion is a required product state but the compact header cannot safely fit a third action. Both reuse approved components/tokens and introduce no new navigation or API.

Real-browser acceptance covers document handoff, fixed scope, answer/reload persistence, reference copying, summary progress/cancel, failure/retry, cross-user safe `404`, durable deletion, drawer Escape/focus return, zero horizontal overflow and Axe WCAG 2 A/AA + 2.1 A/AA with zero serious/critical findings. QA ordinary supporting copy uses `color.text.primary`; the approved secondary token remains unused for small ordinary QA text.

The local MCP bridge used plugin 2.17.0 against Penpot 2.17.2 and displayed a patch-level compatibility warning. Read, write, component-link, bounds and direct-export operations all succeeded and were fresh-read after the final visual cleanup; the warning is tooling provenance, not an approved product-design deviation.

## Learning Notes vertical slice

Validated on 2026-08-31 against Penpot 2.17.2 at final saved source revision `152`. These fifteen top-level boards are the implementation authority for the Notes slice. Page IDs identify the owning page; board IDs identify the exact direct-export source.

| Board | Page / page ID | Viewport | Penpot board ID | Direct export |
|---|---|---:|---|---|
| `Desktop / Notes / Default` | `02 Desktop` / `9b1e7a6b-703c-8060-8008-7071c3463d87` | 1440 × 1024 | `1099f839-63e4-80b7-8008-90947129dd57` | [`desktop-notes.png`](reference/penpot/desktop-notes.png) |
| `Desktop / Notes / Source deleted` | `02 Desktop` / `9b1e7a6b-703c-8060-8008-7071c3463d87` | 1440 × 1024 | `1099f839-63e4-80b7-8008-909480e7b254` | [`desktop-notes-source-deleted.png`](reference/penpot/desktop-notes-source-deleted.png) |
| `Desktop / Notes / Projection failed` | `02 Desktop` / `9b1e7a6b-703c-8060-8008-7071c3463d87` | 1440 × 1024 | `1099f839-63e4-80b7-8008-90948ee9cad4` | [`desktop-notes-projection-failed.png`](reference/penpot/desktop-notes-projection-failed.png) |
| `Desktop / Notes / Clear confirm` | `02 Desktop` / `9b1e7a6b-703c-8060-8008-7071c3463d87` | 1440 × 1024 | `1099f839-63e4-80b7-8008-90949f99978b` | [`desktop-notes-clear.png`](reference/penpot/desktop-notes-clear.png) |
| `Desktop / Notes / Empty` | `02 Desktop` / `9b1e7a6b-703c-8060-8008-7071c3463d87` | 1440 × 1024 | `1099f839-63e4-80b7-8008-9094b0a45de7` | [`desktop-notes-empty.png`](reference/penpot/desktop-notes-empty.png) |
| `Tablet / Notes / Default` | `03 Tablet` / `9b1e7a6b-703c-8060-8008-7071c9876902` | 1024 × 768 | `1099f839-63e4-80b7-8008-90951951e0df` | [`tablet-notes.png`](reference/penpot/tablet-notes.png) |
| `Tablet / Notes / Sources drawer` | `03 Tablet` / `9b1e7a6b-703c-8060-8008-7071c9876902` | 1024 × 768 | `1099f839-63e4-80b7-8008-9095231ebdf8` | [`tablet-notes-sources.png`](reference/penpot/tablet-notes-sources.png) |
| `Tablet / Notes / Projection failed` | `03 Tablet` / `9b1e7a6b-703c-8060-8008-7071c9876902` | 1024 × 768 | `1099f839-63e4-80b7-8008-90952f8a7d54` | [`tablet-notes-projection-failed.png`](reference/penpot/tablet-notes-projection-failed.png) |
| `Tablet / Notes / Empty` | `03 Tablet` / `9b1e7a6b-703c-8060-8008-7071c9876902` | 1024 × 768 | `1099f839-63e4-80b7-8008-90953a56062e` | [`tablet-notes-empty.png`](reference/penpot/tablet-notes-empty.png) |
| `Mobile / Notes / List` | `04 Mobile` / `9b1e7a6b-703c-8060-8008-7071c9888df9` | 390 × 844 | `1099f839-63e4-80b7-8008-9095af8eb216` | [`mobile-notes.png`](reference/penpot/mobile-notes.png) |
| `Mobile / Notes / Editor` | `04 Mobile` / `9b1e7a6b-703c-8060-8008-7071c9888df9` | 390 × 844 | `1099f839-63e4-80b7-8008-9095b5a6954a` | [`mobile-notes-editor.png`](reference/penpot/mobile-notes-editor.png) |
| `Mobile / Notes / Filters drawer` | `04 Mobile` / `9b1e7a6b-703c-8060-8008-7071c9888df9` | 390 × 844 | `1099f839-63e4-80b7-8008-9095bb0121d8` | [`mobile-notes-filters.png`](reference/penpot/mobile-notes-filters.png) |
| `Mobile / Notes / Sources drawer` | `04 Mobile` / `9b1e7a6b-703c-8060-8008-7071c9888df9` | 390 × 844 | `1099f839-63e4-80b7-8008-9095c19ccceb` | [`mobile-notes-sources.png`](reference/penpot/mobile-notes-sources.png) |
| `Mobile / Notes / Version conflict` | `04 Mobile` / `9b1e7a6b-703c-8060-8008-7071c9888df9` | 390 × 844 | `1099f839-63e4-80b7-8008-9095ca611a4f` | [`mobile-notes-conflict.png`](reference/penpot/mobile-notes-conflict.png) |
| `Mobile / Notes / Empty` | `04 Mobile` / `9b1e7a6b-703c-8060-8008-7071c9888df9` | 390 × 844 | `1099f839-63e4-80b7-8008-9095d15efe2b` | [`mobile-notes-empty.png`](reference/penpot/mobile-notes-empty.png) |

### Shared component and token bindings

The boards use linked instances of the established shared library, not detached replacements. The freshly read component-map IDs are Button `9b1e7a6b-703c-8060-8008-70741d401776`, TextField `9b1e7a6b-703c-8060-8008-70744c2d6556`, AppShell `9b1e7a6b-703c-8060-8008-7075be5192e9`, Drawer `9b1e7a6b-703c-8060-8008-70750e4567f4`, and Dialog `size=md` `9b1e7a6b-703c-8060-8008-7074ad832f95`. AppShell instances bind desktop `9b1e7a6b-703c-8060-8008-707574c1a629`, tablet `9b1e7a6b-703c-8060-8008-707593dd9adbe`, and mobile `9b1e7a6b-703c-8060-8008-7075b44513a2`; Button primary binds `9b1e7a6b-703c-8060-8008-70740e0b1a82`. Empty boards also reuse EmptyState `9b1e7a6b-703c-8060-8008-707540e5d3c1`. No Notes-specific library component was introduced because the slice is fully expressed by these shared primitives; `tests/design/test_penpot_component_map.mjs` pins the five runtime-mapped shared IDs used by Notes.

| Shared component | Fresh-read Penpot ID |
|---|---|
| `Button` | `9b1e7a6b-703c-8060-8008-70741d401776` |
| `TextField` | `9b1e7a6b-703c-8060-8008-70744c2d6556` |
| `AppShell` | `9b1e7a6b-703c-8060-8008-7075be5192e9` |
| `Drawer` | `9b1e7a6b-703c-8060-8008-70750e4567f4` |
| `Dialog / md` | `9b1e7a6b-703c-8060-8008-7074ad832f95` |

Visible geometry and styling bind the existing `color.canvas`, `color.surface`, `color.border`, `color.brand.100`, `color.brand.600`, `color.brand.700`, `color.text.primary`, `color.warning`, `color.danger`, `radius.md`, `radius.lg`, `radius.pill`, and `space.2` semantic tokens. Status meaning is always repeated in text and is not conveyed by color alone.

### State, responsive and interaction contract

- Desktop keeps the 248 px navigation and 64 px top bar, then exposes the Notes list, editor and read-only source column simultaneously. The source-deleted state renders only the tombstone text “来源已删除”; deleted filename, page, citation and excerpt data are absent.
- Tablet keeps the 72 px rail and a two-column list/editor workspace. Sources use the linked right Drawer, move focus into the drawer, trap focus while open, close on `Escape`, and return focus to the source trigger.
- Mobile is intentionally list → editor rather than a compressed multi-column canvas. Filters and sources use bottom drawers above the 64 px bottom navigation. Named actions are at least 44 × 44 and preserve visible focus treatment.
- Projection failure is a non-blocking warning with an explicit retry action. Note reading, editing, source viewing and deletion remain available; a projection failure never substitutes for or rolls back the saved note.
- Clear confirm uses the linked medium Dialog and explicitly limits the destructive action to Notes; documents and QA records are not deleted. The source-deleted tombstone remains non-destructive to the note itself.
- Version conflict preserves the local Markdown draft and offers “复制本地草稿” or “重新加载版本”. Implementation must never silently merge or overwrite the local draft.
- Empty states expose “新建笔记” and “从 QA 记录”. The latter starts an explicit user action and does not seed a production note automatically.

Notes boards use illustrative sample data only; production renders authenticated server records and never seeds these values.

### Fresh-read, cleanup and export evidence

At revision `152`, fresh readback confirmed the same seven page IDs and all fifteen Notes board records, including `Mobile / Notes / Version conflict` at `390 × 844` with board ID `1099f839-63e4-80b7-8008-9095ca611a4f`. The comprehensive page-scoped audit from revision `151` remains valid for the fourteen unchanged boards: exactly 5 desktop, 4 tablet and 6 mobile Notes boards, no duplicate board names, root-link counts of 5/4/6/6/5 for desktop Default/Source deleted/Projection failed/Clear confirm/Empty, 5/7/6/5 for tablet Default/Sources drawer/Projection failed/Empty, and 4/6/8/8/7/6 for mobile List/Editor/Filters drawer/Sources drawer/Version conflict/Empty. Full instance readback found zero broken component links on every board. Visible text-bounds overflow, visible actual-bounds overflow and overlay-order violations were zero on all fifteen boards. The mobile audit found 6/5/11/7/7/5 named actions respectively, with zero below 44 × 44.

Revision `152` extends the conflict-state audit to the three foreground siblings whose geometric overlap is not detected by containment-only checks:

| Conflict sibling | Penpot shape ID | Final bounds |
|---|---|---|
| `复制本地草稿 action` | `1099f839-63e4-80b7-8008-9095d05fe01e` | `x=64, y=11460, w=140, h=44` |
| `重新加载服务器版本 action` | `1099f839-63e4-80b7-8008-9095d05fe01c` | `x=212, y=11460, w=140, h=44` |
| `本地草稿保留说明` | `1099f839-63e4-80b7-8008-9095d0effbbf` | `x=40, y=11520, w=310, h=30` |

The final audit checks all `3` unique sibling pairs and records `0` intersections; the two actions end at `y=11504`, the preservation message begins at `y=11520`, and the minimum vertical gap is `16 px`. Fresh text readback reports actual bounds `x=97.5, y=11525.703125, w=195, h=18.5`, fully contained in both the formal text box and the mobile board.

Two final visual cleanup passes were applied before export: EmptyState instances now use the Notes-specific visible “新建笔记” label with a 44 px action instead of inherited legacy-route copy; projection-failure boards now separate the warning, concept chips and editor body with explicit gaps, and the stray desktop concept label was removed. Typography uses the established 1.2 line-height rhythm, and the compact mobile EmptyState copy remains constrained within its card.

All fifteen PNGs were exported directly from their recorded Penpot board IDs and decode at their exact named dimensions. Fourteen unchanged exports retain their revision-`151` pixels; `mobile-notes-conflict.png` alone was directly re-exported from revision `152` after the spacing correction. The revised `390 × 844` PNG was inspected at original size and shows the two 44 px conflict actions, preservation message and 16 px separation without clipping, overlap, missing glyphs or obscured actions. No browser-only visual divergence is approved yet; implementation packets must compare authenticated runtime data against these exports while preserving the state meaning and responsive structure above.
