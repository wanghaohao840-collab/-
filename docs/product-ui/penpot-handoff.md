# 知研 Penpot product UI handoff

Validated on 2026-08-11 against Penpot 2.17.1 at file revision `89`. Penpot is the sole design source for this product UI; the former design file is an archive only.

The document-library vertical slice was fresh-read and directly exported on 2026-08-22 against Penpot 2.17.2 at final file revision `115`.

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

These local library components live on `01 Components` (`9b1e7a6b-703c-8060-8008-7071c343b8c2`) under the `DocumentLibrary` path. The component ID is used to create linked instances; the main shape ID is the editable source.

| Component | Component ID | Main shape ID | Source size | Bound tokens |
|---|---|---|---:|---|
| `DocumentRow` | `f35db4ee-075c-8075-8008-7c1eea45d28f` | `f35db4ee-075c-8075-8008-7c1ee50626f5` | 1080 × 84 | `color.border`, `color.brand.100`, `color.brand.700`, `color.surface`, `color.text.primary`, `radius.md`, `radius.pill` |
| `ImportTaskRow` | `f35db4ee-075c-8075-8008-7c1eefe300f4` | `f35db4ee-075c-8075-8008-7c1eea61d96f` | 1080 × 96 | `color.border`, `color.brand.100`, `color.brand.600`, `color.brand.700`, `color.surface`, `color.text.primary`, `radius.md`, `radius.pill`, `space.2` |
| `FilePicker` | `f35db4ee-075c-8075-8008-7c1ef1831cf3` | `f35db4ee-075c-8075-8008-7c1eeffad986` | 560 × 180 | `color.border`, `color.brand.600`, `color.surface`, `color.text.primary`, `radius.md`, `space.2` |

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

At revision `115`, fresh readback found 6/6/6 linked component roots on the desktop/tablet/mobile complete boards, 4 on empty, 7 on importing, 5 on partial failure and 5 on the mobile import sheet, with zero broken component-root links. Visible text-bounds overflow and actual-bounds overflow are zero on all seven boards and the three new component masters. The mobile complete and import-sheet audits found 13 and 10 named interactive targets respectively, with zero below 44 × 44. All seven PNGs were exported directly at original board size and visually checked for clipping, alignment, missing glyphs and state clarity. Browser output should match these boards except for the documented font-rendering differences below; no additional document-library deviation is approved.

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
