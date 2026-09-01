---
id: "notes-vertical-slice-07"
title: "校正 Notes 三档运行时视觉"
status: "done"
parallel-safe: false
depends-on: ["notes-vertical-slice-04", "notes-vertical-slice-05"]
base-commit: "9f7ba2c"
owner: "unassigned"
---

# Task Packet: 校正 Notes 三档运行时视觉

## Goal

让真实认证数据驱动的 `/notes` 工作台在桌面、平板和手机三档中匹配 Penpot revision `153` 的 Notes 画板结构、层级、间距、响应式状态与交互含义，使 Packet 06 能在不接受浏览器端偏差的前提下创建并评审视觉基线。

## Non-goals

- 不改变 Note 数据模型、API、持久化、来源解析、版本冲突、投影或清空语义。
- 不复制 Penpot 示例数据或“示例数据·仅用于设计预览”标记到生产运行时。
- 不创建、复制或更新 Playwright 基线截图；基线仍由 Packet 06 在本包通过后评审生成。
- 不重构 AppShell、共享 Button 或其他产品路由。

## Delivery context

Packet 06 的真实桌面 E2E 在 Axe 通过后生成了 runtime actual，并在原始尺寸与 `docs/product-ui/reference/penpot/desktop-notes.png` 对照。当前实现虽有 248 px 导航和 list/editor/source 三列，但筛选行、标题/操作、列宽、编辑器元数据、来源卡与空白层级存在实质偏差；`docs/product-ui/penpot-handoff.md` 明确没有批准 browser-only divergence。Packet 06 已在 commit `9f7ba2c` 透明阻塞，本包只校正 UI，完成后 Packet 06 恢复发布门禁。

## Relevant files and current interfaces

- `web/src/pages/NotesPage.tsx:32-92` — 拥有 URL 筛选、QA 预填、新建态与工作台组合；checkpoint 已修复带来源预填时的 `selectedId="new"` 语义，必须保留。
- `web/src/components/NotesWorkspace/NotesWorkspace.tsx:29-91` — 拥有工具栏、list/editor/source 响应式组合、抽屉/对话框、dirty/conflict/projection 状态。
- `web/src/components/NotesWorkspace/NoteList.tsx` — 列表卡、空状态、数量与新建/QA 动作。
- `web/src/components/NotesWorkspace/NoteEditor.tsx` — 概念、标签、Markdown 编辑/预览、保存/删除与已保存状态。
- `web/src/components/NotesWorkspace/NoteSourcePanel.tsx` — 桌面来源列和 tablet/mobile drawer 的同一事实来源。
- `web/src/components/NotesWorkspace/notes-workspace.css` — 当前列宽、筛选、三档断点与小字 token；checkpoint 已修复普通小字对比度，禁止回退到 `color.text.secondary`。
- `web/e2e/notes.spec.ts:302-338` — Packet 06 已提交的真实服务器 Axe/overflow/44px/截图消费者；本包只运行，不修改。
- `docs/product-ui/penpot-handoff.md` 的 `Learning Notes vertical slice` — revision `153`、15 个权威 board ID、响应式/状态合同和无 browser-only divergence 约束。
- 视觉权威：`docs/product-ui/reference/penpot/desktop-notes*.png`、`tablet-notes*.png`、`mobile-notes*.png`。
- Existing changes to preserve: controller-owned `.superpowers/sdd/progress.md` 和 untracked `REVIEW.md`；Packet 06 checkpoint `9f7ba2c` 的 E2E、QA 来源预填修复、对比度修复与文档证据。

## Prerequisites

### Packet dependencies

- `notes-vertical-slice-04`、`notes-vertical-slice-05` 必须为 `done`。
- Packet 06 blocked checkpoint `9f7ba2c` 必须存在，提供真实 visual consumer 和已批准窄修复。

### Repository/base state

- Base commit: `9f7ba2c`。
- `/notes` 的领域/API/React 功能测试必须保持通过；QA prefill source 必须仍进入显式 create POST。

### External prerequisites

- npm dependencies 与 Chromium 可用。
- 本包不要求 Docker；Penpot revision `153` 的直接导出已在仓库，若连接仍可用可只读复核，不能修改设计源来迁就 runtime。

## Explicit change boundary

### Allowed files

- Modify: `docs/agent-workflow/task-packets/2026-08-30-notes-vertical-slice/07-notes-visual-alignment.md`（状态和 handoff）
- Modify: `web/src/pages/NotesPage.tsx`
- Modify: `web/src/pages/NotesPage.test.tsx`
- Modify: `web/src/components/NotesWorkspace/NotesWorkspace.tsx`
- Modify: `web/src/components/NotesWorkspace/NotesWorkspace.test.tsx`
- Modify: `web/src/components/NotesWorkspace/NoteList.tsx`
- Modify: `web/src/components/NotesWorkspace/NoteEditor.tsx`
- Modify: `web/src/components/NotesWorkspace/NoteSourcePanel.tsx`
- Modify: `web/src/components/NotesWorkspace/NoteClearDialog.tsx`
- Modify: `web/src/components/NotesWorkspace/notes-workspace.css`

### Allowed behavior changes

- 重新组织现有筛选、标题、列表、编辑器、来源与动作的可视层级和响应式呈现。
- 在不新增 API 字段的前提下，将现有 query/tags/source controls 呈现为与 Penpot 同层级的搜索和紧凑筛选控制；所有现有筛选能力必须仍可达。
- 增加仅用于无障碍/布局验证的语义属性或稳定 class；不增加产品数据或持久化。

### Forbidden changes

- 不修改 `web/e2e/notes.spec.ts`、任何 screenshot baseline、AppShell、API、Python、数据库、tokens 或 Penpot 文件/导出。
- 不删除 tag/source/query 筛选、Markdown preview、dirty guard、explicit save、版本冲突、投影失败、来源 tombstone、clear/delete confirmation、focus trap 或 44 px 触控目标。
- 不把示例 board 的文件名、正文、标签或提示 badge 写入生产种子/fallback。
- 不用 absolute positioning 对单张截图硬编码；布局必须对真实数据长度和三个断点稳定。

## Interface contract

### Consumes

- `NotesWorkspace` 当前 props、`NoteSaveInput`、`Note`/`NoteListItem` DTO 和 Packet 06 的真实 E2E。
- AppShell 已有 desktop `248px` navigation、tablet `72px` rail、mobile `64px` bottom nav 与 `64px` top bar。
- Penpot revision `153` 的 5 desktop、4 tablet、6 mobile Notes boards。

### Produces

- Desktop：页面标题/说明和搜索/紧凑筛选/新建动作形成同一 header 区；随后同时呈现约 `300px` list、流体 editor（权威画板约 `480px`）和约 `268px` read-only source，间距与权威画板一致。
- Tablet：header 后为约 `300px` list + 流体 editor 两列；来源只通过右侧 drawer 打开，不保留第三列。
- Mobile：`24px` 内容边距，list 与 editor 互斥；list 顶部为搜索、筛选入口和全宽 44px 新建；editor 顶部为返回/标题/保存，来源和删除位于底部动作；drawer 位于 64px bottom nav 之上。
- 所有普通 12–16px 文案/control label 使用可达 WCAG AA 的现有语义 token。

### Invariants

- Production 继续只渲染认证服务器记录；不产生示例数据。
- URL/filter/source prefill、explicit save、optimistic concurrency、tombstone 和 projection 行为不变。
- Desktop/tablet/mobile 均无页面级横向溢出；移动主动作至少 `44×44`。
- 状态含义使用文本/图标，不依赖颜色；drawer/dialog 保留 focus trap、Escape close、focus return。

## Required behavior

- Desktop runtime 必须移除当前“独立顶置三控件行 + 下方重复标题”的层级，改为 Penpot 的标题→搜索/筛选/新建→工作区顺序。
- Desktop list/editor/source 必须是三个清晰独立 surface；列表卡密度、编辑正文区、来源卡/查看动作和底部保存状态在原始尺寸与 board 的结构相符。
- Tablet 不显示固定来源列；“查看来源”打开 drawer，editor/list 仍在 `1024×768` 内完整可用。
- Mobile list 不压缩桌面网格；editor 不显示 list/source 固定列，返回、保存、来源、删除均可达且不被 bottom nav 遮挡。
- Empty、source-deleted、projection-failed、clear-confirm、filters drawer、sources drawer 和 version-conflict 保持各自权威状态含义。
- 真实内容可能与 Penpot 示例不同；比较重点是 hierarchy、surface bounds、spacing、responsive composition、state meaning 与动作位置。

## Implementation guidance

1. 先在 `NotesPage.test.tsx`/`NotesWorkspace.test.tsx` 增加结构和断点所需语义断言，保留 checkpoint 的 QA prefill 回归。
2. 将 desktop 筛选 controls 纳入 `NotesWorkspace` header 结构，避免 NotesPage 外层重复视觉层级；移动端仍使用现有 filters drawer。
3. 复用现有组件和 props，优先 CSS grid/flex 与语义 tokens，不引入新的状态容器或 API。
4. 对照原始尺寸 PNG 调整 desktop/tablet/mobile；分别检查 Default、Empty、projection/source/conflict/drawer 状态，不只修默认桌面。
5. 用 Packet 06 visual test 生成 ignored actual；预期在 snapshot 仍缺失处停止，但 Axe、overflow、44px 和到达截图前的交互不得失败。不要运行 `--update-snapshots`。

## Acceptance criteria

- [ ] Desktop actual 与 `desktop-notes.png` 在 header、三列 surface、动作层级和间距上无未解释的实质差异。
- [ ] Tablet actual 与 `tablet-notes.png` 的 rail + list/editor + source drawer 合同一致；无固定第三列。
- [ ] Mobile list/editor actual 与两个权威 PNG 的单列、24px padding、44px 动作和 bottom-nav 避让一致。
- [ ] 15 个 Notes 状态的结构/交互含义仍可由单元测试和现有 UI 实现决定，尤其 tombstone、projection、clear、drawers、conflict、empty。
- [ ] QA prefill source、explicit save、dirty guard、筛选和用户数据路径无回归。
- [ ] Axe serious/critical 为零、无页面横向 overflow、移动可见 primary actions 均不少于 `44×44`。

## Test and verification commands

```powershell
Set-Location web
npx vitest run src/pages/NotesPage.test.tsx src/components/NotesWorkspace/NotesWorkspace.test.tsx
npm test -- --run
npm run typecheck
npm run lint
npm run build
npx playwright test e2e/notes.spec.ts --project=desktop --project=tablet --project=mobile --workers=1 -g "approved visuals"
Set-Location ..
git diff --check
```

Expected: Vitest/full frontend/typecheck/lint/build/diff check PASS。Playwright 必须通过真实登录、Axe、overflow、44px 和状态导航；因为 Packet 06 尚未批准 baseline，若只在 `toHaveScreenshot` 报 missing snapshot，可将其作为生成 actual 的预期阻塞证据。必须用原始尺寸查看 desktop/tablet/mobile actual 并在 handoff 逐项记录与 Penpot 的结构对照；任何更早失败或实质视觉差异都表示本包未完成。

## Stop conditions

如果需要修改 E2E、截图 baseline、AppShell、API/DTO/Python/token/Penpot，或无法在三个断点保留既有交互与数据合同，则追加 reality-conflict report 并停止，不得扩边。

## Implementation handoff

按 `docs/agent-workflow/README.md` 的完整格式记录文件、结构对照、三个 actual 的绝对路径、Axe/overflow/44px 证据、命令/计数、偏差、风险和 commit。

## Implementation handoff

- Status: done
- Files changed:
  - `web/src/pages/NotesPage.tsx`
  - `web/src/components/NotesWorkspace/NotesWorkspace.tsx`
  - `web/src/components/NotesWorkspace/NotesWorkspace.test.tsx`
  - `web/src/components/NotesWorkspace/NoteList.tsx`
  - `web/src/components/NotesWorkspace/NoteEditor.tsx`
  - `web/src/components/NotesWorkspace/notes-workspace.css`
  - `docs/agent-workflow/task-packets/2026-08-30-notes-vertical-slice/07-notes-visual-alignment.md`
- Acceptance criteria:
  - [x] The page header now owns title, live search, compact filters and the new-note action; no duplicated `NotesPage` filter row remains.
  - [x] Desktop uses separate 300 px list, fluid editor and 268 px source surfaces; tablet suppresses the third column and uses the existing source drawer.
  - [x] Mobile uses a 24 px effective Notes gutter, a list/editor composition, full-width new-note action, filter drawer trigger, editor back action and bottom-nav avoidance.
  - [x] QA prefill/new selection, explicit save, dirty guard, conflict, projection, tombstone, clear, filter and focus-trap tests remain green.
  - [x] Original-size actuals were inspected: `C:\Users\11272\AppData\Local\Temp\zhiyan-notes-packet07-actuals-20260901\notes-default-desktop-actual.png`, `C:\Users\11272\AppData\Local\Temp\zhiyan-notes-packet07-actuals-20260901\notes-default-tablet-actual.png`, and `C:\Users\11272\AppData\Local\Temp\zhiyan-notes-packet07-actuals-20260901\notes-list-mobile-actual.png`. Compared with the direct Penpot exports, all three have the required page/header-to-workspace sequence, distinct desktop surfaces, tablet two-column composition and mobile single-column/list hierarchy; runtime text/source availability deliberately differs because it is authenticated server data.
- Verification:
  - `npx vitest run src/pages/NotesPage.test.tsx src/components/NotesWorkspace/NotesWorkspace.test.tsx` — PASS (2 files, 18 tests)
  - `npm test -- --run` — PASS (18 files, 157 tests)
  - `npm run typecheck` — PASS
  - `npm run lint` — PASS
  - `npm run build` — PASS (existing Vite >500 kB chunk warning only)
  - `npx playwright test e2e/notes.spec.ts --project=desktop --project=tablet --project=mobile --workers=1 -g "approved visuals" --update-snapshots=none` — reaches only missing-snapshot assertions after the real-login, Axe, overflow and mobile-44px checks; no baselines were written. Individual no-write runs produced the three actuals above.
  - `git diff --check` — PASS
- Deviations:
  - No product/API/state divergence. The visual consumer has no approved Packet 06 baseline, so its required terminal assertion is intentionally a missing snapshot. A first local run without `--update-snapshots=none` generated four untracked files; the controller moved them to recoverable quarantine outside the worktree before the recorded no-write runs.
- Residual risks:
  - Packet 06 must create and review its baselines from the no-write actuals. The package's visual consumer does not reach the later mobile-editor assertion without a baseline for the preceding list assertion, so the current authenticated mobile list actual is recorded here; the editor is still covered by the existing responsive composition and focused component tests.
- Commit:
  - pending

## Final corrective self-review (2026-09-01)

- Status: done
- Scope decision:
  - Removed the screenshot-specific `translateY` positioning from the Notes header. Desktop actions now occupy the explicit filter row through CSS Grid; tablet actions remain in the title row through the responsive override, and mobile keeps its existing single-column composition.
  - Removed the extra `saveFeedback`/`savedId` state and preserved the Packet04/05 saved-status copy (`已保存`) and explicit-save lifecycle. No API, DTO, persistence, E2E, AppShell, token or Penpot files were changed.
  - Added an explicit tablet filter-row span so search, tag and source controls do not wrap into an unexplained third header row at `1024 × 768`.
- Final actual evidence (generated by the no-write visual command; not baselines):
  - Desktop: `D:\python_self_agent\.worktrees\release-document-library\web\test-results\notes-Notes-approved-visua-1d7dd--and-mobile-primary-targets-desktop\test-failed-1.png`
  - Tablet: `D:\python_self_agent\.worktrees\release-document-library\web\test-results\notes-Notes-approved-visua-1d7dd--and-mobile-primary-targets-tablet\test-failed-1.png`
  - Mobile list: `D:\python_self_agent\.worktrees\release-document-library\web\test-results\notes-Notes-approved-visua-1d7dd--and-mobile-primary-targets-mobile\test-failed-1.png`
  - These were inspected at original size against `docs/product-ui/reference/penpot/desktop-notes.png`, `tablet-notes.png` and `mobile-notes.png`. Desktop retains the 300/480/268 surface hierarchy and filter-row action placement; tablet retains rail + 300px list/editor with no fixed source column and a non-wrapping filter row; mobile retains the 24px effective gutter, mutually exclusive list/editor, 44px new-note/filter actions and bottom-nav clearance. Runtime source/text differences are authenticated-data differences, not design-state substitutions.
- Acceptance criteria:
  - [x] Desktop/tablet/mobile hierarchy, surface bounds, responsive composition and spacing were inspected against the direct Penpot revision-153 exports; no unexplained material mismatch remains after the final grid correction.
  - [x] Existing 15-state behavior and Packet04/05 contracts remain covered by the focused tests and unchanged API/data boundaries.
  - [x] QA prefill source, explicit save, dirty guard, conflict, projection, tombstone, clear, filters and drawer focus behavior remain covered.
  - [x] Visual Playwright reached Axe serious/critical zero, page overflow zero and mobile visible-primary-target 44px checks for all three projects, then stopped only at missing-snapshot assertions. No snapshot directory was created or updated.
- Verification:
  - `npx vitest run src/pages/NotesPage.test.tsx src/components/NotesWorkspace/NotesWorkspace.test.tsx` — PASS (2 files, 18 tests).
  - `npm test -- --run` — parallel run intermittently failed only in unrelated `src/auth/ProtectedRoute.test.tsx` (155–156/157); isolated `npx vitest run src/auth/ProtectedRoute.test.tsx` passed 4/4.
  - `npx vitest run src tests --run --no-file-parallelism` — PASS (18 files, 157 tests).
  - `npm run typecheck` — PASS.
  - `npm run lint` — PASS.
  - `npm run build` — PASS (existing Vite chunk-size warning only).
  - `npx playwright test e2e/notes.spec.ts --project=desktop --project=tablet --project=mobile --workers=1 -g "approved visuals" --update-snapshots=none` — expected terminal missing snapshots only: 3 failed at `toHaveScreenshot` because Packet06 baselines do not exist; all pre-screenshot Axe, overflow, 44px and navigation checks passed; no baselines written.
  - `git diff --check` — PASS.
- Deviations:
  - None from the visual/data contract. The parallel Vitest failure is a pre-existing test-order/interference issue; the serialized full frontend suite is green.
- Residual risks:
  - Packet06 must create and review its approved runtime baselines from the three no-write actuals before release acceptance; this packet intentionally does not create them.
- Commit:
  - pending (recorded in the final delivery response to avoid a self-referential packet edit).

## Reviewer-finding corrective handoff (2026-09-01)

- Status: done
- Scope decision:
  - Reviewed only the permitted corrective diff in `web/src/components/NotesWorkspace/NotesWorkspace.test.tsx` and `web/src/components/NotesWorkspace/notes-workspace.css`.
  - Kept the responsive grid correction: desktop now uses shrink-safe `minmax(240px, 300px) minmax(0, 1fr) minmax(220px, 268px)` instead of the prior 300px / 420px / 268px hard minima; the middle column therefore remains fluid rather than forcing page overflow between the desktop and tablet breakpoints.
  - Kept the tablet filter override (`grid-column: 1 / -1`) and its compact toolbar spacing. The fixed source surface is still suppressed below 1200px. Removed `.notes-saved-status`; no selector remains in the source tree.
  - No further code change was required after real-runtime verification; no E2E, baseline, AppShell, API, data, token, or controller-owned file was touched.
- Runtime layout evidence (real Notes runtime, authenticated record, Playwright Chromium; one-time script: `C:\Users\11272\AppData\Local\Temp\notes-layout-evidence.mjs`; no screenshot actual/baseline was created):
  - Desktop `1366×900`: `documentElement.scrollWidth/clientWidth = 1366/1366`; workspace x/width = `296/1022`; list x/width = `296/300`; editor x/width = `620/406`; source x/width = `1050/268`. All three surfaces share y=`261.59`, with 24px gaps (`596→620`, `1026→1050`), so they are visible and non-overlapping while retaining the required approximately 300/fluid/268 hierarchy.
  - Tablet `1024×768`: `documentElement.scrollWidth/clientWidth = 1024/1024`; fixed source computed `display=none`, width=`0`; list/editor are `x=104,width=300` and `x=428,width=564`. Search, tag, and source filter labels all have top y=`147.98` (one row); workspace top is y=`96`, filters bottom y=`219.98`, and grid top y=`235.98`.
- Verification:
  - `npm run build` — PASS (389 modules; existing Vite >500 kB chunk warning only; required to serve the current runtime).
  - `node C:\Users\11272\AppData\Local\Temp\notes-layout-evidence.mjs` — PASS; produced the coordinate and overflow evidence above from the current built Notes runtime. No temporary screenshot actual exists.
  - `npx vitest run src/pages/NotesPage.test.tsx src/components/NotesWorkspace/NotesWorkspace.test.tsx` — PASS (2 files, 18 tests).
  - `npm run typecheck` — PASS.
  - `npm run lint` — PASS.
  - `git diff --check` — PASS.
- Deviations:
  - None. The temporary runtime data directory is OS-managed under `%TEMP%`; it is not a repository artifact or test baseline.
- Residual risks:
  - Packet 06 still owns screenshot-baseline creation and review. This corrective handoff intentionally supplies geometry evidence only and does not update snapshots.
- Commit:
  - prior implementation: `94b60ec` (`fix: align notes workspace with approved design`)
  - reviewer-finding correction: pending (`fix: stabilize notes responsive layout`)
