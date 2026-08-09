# 知研 Penpot product UI handoff

Validated on 2026-08-06 against Penpot 2.17.1. Penpot is the sole design source for this product UI; the former design file is an archive only.

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

## Reference boards

| Reference | Viewport | Penpot board ID | Export |
|---|---:|---|---|
| Desktop login | 1440 × 1024 | `9b1e7a6b-703c-8060-8008-70761a57accd` | [`desktop-login.png`](reference/penpot/desktop-login.png) |
| Desktop AppShell | 1440 × 1024 | `9b1e7a6b-703c-8060-8008-70768eb3fbd8` | [`desktop-shell.png`](reference/penpot/desktop-shell.png) |
| Tablet AppShell | 1024 × 768 | `9b1e7a6b-703c-8060-8008-7076da187916` | [`tablet-shell.png`](reference/penpot/tablet-shell.png) |
| Mobile login | 390 × 844 | `9b1e7a6b-703c-8060-8008-707701065fab` | [`mobile-login.png`](reference/penpot/mobile-login.png) |
| Mobile AppShell | 390 × 844 | `9b1e7a6b-703c-8060-8008-7077227fbcfe` | [`mobile-shell.png`](reference/penpot/mobile-shell.png) |
| Session expired | 1440 × 1024 | `9b1e7a6b-703c-8060-8008-70776404ef6f` | [`session-expired.png`](reference/penpot/session-expired.png) |

The six exports were generated directly from these boards at their original dimensions and visually checked for clipping, overflow, alignment, contrast, and missing glyphs.

## Responsive and navigation rules

- Desktop (`≥1200 px`): fixed 248 px sidebar, 64 px top bar, and content capped at 1200 px.
- Tablet (`768–1199 px`): 72 px compact rail and on-demand drawer. Use two columns only while each remains at least 320 px.
- Mobile (`≤767 px`): one content column, 64 px bottom navigation, and controls with a minimum 44 px target. The linked Login/Register remember rows read back at `220 × 44`, PasswordField visibility controls at `44 × 44`, and Mobile AppShell `/legacy` actions at `140 × 44`.
- Desktop navigation: 概览、文档库、智能问答、文献检索、学习笔记、学习洞察.
- Mobile navigation: 概览、文档、问答、检索、更多. The 更多 drawer contains 学习笔记、学习洞察、账户、退出登录.
- Unmigrated capability copy: “该能力正在迁移到新版界面，可暂时前往旧版使用。” The secondary action routes to `/legacy`.

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

This UI handoff does not alter ApplicationServices, authentication/CSRF behavior, per-user UUID storage, `document_id`, citation, RAG, Memory, reporting, bulk-import, or storage boundaries. The legacy Gradio experience remains available only through `/legacy`.
