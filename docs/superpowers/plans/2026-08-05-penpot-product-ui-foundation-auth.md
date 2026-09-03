# 知研 Penpot 产品 UI 与认证壳 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以 Penpot 作为唯一设计源，交付“知研 · 智能文档学习助手”的 DTCG Token、公共组件、三档响应式认证/AppShell、React 前端、统一 FastAPI/Gradio 运行时及可重复的设计到代码验收链路。

**Architecture:** Penpot Remote MCP 负责设计文件、Token、组件、Variants、页面和真实节点 ID；DTCG JSON 是 Penpot 发布到仓库的版本化快照，由确定性脚本生成 CSS。React 只通过 FastAPI `/api/v1` 使用现有共享 `ApplicationServices`、HttpOnly Cookie 和内存 CSRF；生产运行时由一个 Uvicorn Worker 同时承载 API、React SPA 和 `/legacy` Gradio。Penpot 组件 ID 与 React 文件通过仓库内映射清单关联，Playwright/axe/视觉基线替代 Figma Code Connect。

**Tech Stack:** Penpot Cloud + Remote MCP、W3C DTCG JSON、Node.js 22、React 19、TypeScript 5、Vite 7、React Router 7、TanStack Query 5、Vitest、Testing Library、Playwright、axe-core、Python 3.12 本地 venv、Python 3.11 容器、FastAPI、Uvicorn、Gradio 6.19、pytest。

## Global Constraints

- 实施前完整阅读 `PROJECT_KNOWLEDGE.md`；当前代码、测试、配置和运行行为优先于历史说明。
- Penpot 是唯一设计源；现有 Figma 文件只读归档，不参与同步、实现或验收。
- Penpot 云端文件名固定为“知研 · 智能文档学习助手”，页面固定为 `00 Foundations`、`01 Components`、`02 Desktop`、`03 Tablet`、`04 Mobile`、`05 States`、`06 Handoff`。
- Penpot Remote MCP URL 含个人密钥，只能配置在 Codex MCP 设置中；不得写入仓库、日志、截图、测试输出或对话。
- MCP 写入前先只读确认当前文件和当前页面；连接中断、活动标签页变化或读取不完整时立即停止，不猜测文件、页面、组件或画板 ID。
- 三档验收视口固定为桌面 `1440x1024`、平板 `1024x768`、手机 `390x844`。
- 品牌固定为“知研”，副标题固定为“智能文档学习助手”，采用简洁、专业、翡翠绿色的学术工具视觉方向。
- Token 发布方向固定为 `Penpot -> design/tokens/zhiyan.tokens.json -> web/src/styles/tokens.css`；禁止页面组件散落品牌色、间距和圆角硬编码，禁止代码反向覆盖 Penpot Token。
- 不创建 `.figma.tsx`，不运行 Figma Code Connect，不把 Figma URL 或节点作为验收依据。
- 保持 `UI/API -> Session/Application Services -> Assistant -> Tool -> Memory/RAG/Storage` 单向依赖。
- 不改变 `document_id` 隔离、用户 UUID 数据目录、来源页码、引用、RAG、Memory、报告、批量导入任务和存储契约。
- 单进程、单 Uvicorn Worker；FastAPI 与 `/legacy` 共享同一组 SessionRegistry、RuntimeRegistry、ImportTaskRepository、ImportWorkerPool 和 ImportTaskService。
- 会话令牌只存放在 `zhiyan_session` HttpOnly、SameSite=Lax、path=/ Cookie；生产 HTTPS 由 `APP_COOKIE_SECURE=true` 启用 Secure。
- 所有改变状态的 `/api/v1` 请求必须验证 `X-CSRF-Token`，注册、登录和健康检查除外。
- API 错误保持 `{"error":{"code":"example_code","message":"用户可读信息","retryable":false,"field_errors":{}}}` 形状，不泄漏令牌、密码、UUID、运行路径或 Assistant 对象。
- 已完成且已复审的后端提交保持不变：`0aa2d83`、`91083d5`、`84cc8e4`、`1316b41`、`73290a7`。

---

## File Responsibility Map

| Path | Responsibility |
|---|---|
| `design/tokens/zhiyan.tokens.json` | Penpot 发布的规范化 DTCG Token 快照 |
| `scripts/design_tokens.mjs` | 校验 Token 并确定性生成 CSS |
| `tests/design/test_design_tokens.mjs` | Token 结构、值、生成和漂移测试 |
| `docs/product-ui/penpot-handoff.md` | Penpot 文件、页面、画板、组件 ID 与交付规则 |
| `docs/product-ui/penpot-component-map.json` | Penpot 组件到 React 文件/Props 的映射 |
| `docs/product-ui/penpot-component-map.schema.json` | 映射清单 JSON Schema |
| `docs/product-ui/reference/penpot/*.png` | 经检查的 Penpot 三视口参考图 |
| `web/src/api/client.ts` | 唯一类型化 HTTP/错误/CSRF 边界 |
| `web/src/auth/AuthProvider.tsx` | 内存会话状态、登录、注册、退出和 401 清理 |
| `web/src/auth/ProtectedRoute.tsx` | 受保护路由跳转与 intended-location 恢复 |
| `web/src/components/*` | 由 Token 驱动的公共 React 组件 |
| `web/src/layout/navigation.ts` | 桌面、平板、手机共享的唯一导航定义 |
| `web/src/layout/AppShell.tsx` | 三档响应式应用壳 |
| `web/src/pages/*` | 登录、注册和功能迁移页 |
| `server.py` | 单进程生产 ASGI 入口 |
| `api/app.py` | API 路由、生命周期和统一应用装配 |
| `ui/gradio_app.py` | 现有 Gradio 树的可挂载工厂 |
| `web/e2e/*` | 用户流程、axe、视觉和三视口验收 |

---

### Task 1: 在 Penpot 原生重建 Foundations、组件和三档认证/AppShell

**Files:**
- Create: `design/tokens/zhiyan.tokens.json`
- Create: `docs/product-ui/penpot-handoff.md`
- Create: `docs/product-ui/reference/penpot/desktop-login.png`
- Create: `docs/product-ui/reference/penpot/desktop-shell.png`
- Create: `docs/product-ui/reference/penpot/tablet-shell.png`
- Create: `docs/product-ui/reference/penpot/mobile-login.png`
- Create: `docs/product-ui/reference/penpot/mobile-shell.png`
- Create: `docs/product-ui/reference/penpot/session-expired.png`
- External create/update: Penpot Cloud file `知研 · 智能文档学习助手`

**Interfaces:**
- Consumes: approved migration spec `docs/superpowers/specs/2026-08-05-penpot-product-ui-migration-design.md`.
- Produces: real Penpot file/page/component/board IDs, normalized DTCG JSON and approved reference PNGs used by Tasks 2, 4 and 6.

- [ ] **Step 1: Verify the Remote MCP prerequisite read-only**

Confirm Penpot MCP tools are listed. If the target file does not exist, create one blank Penpot Cloud design through the authenticated Penpot web UI and name it exactly `知研 · 智能文档学习助手`. Open that file, connect MCP from the active tab, then run the available read-only overview operation and verify the returned file name.

Expected: the active file can be read. If the tools are absent, the plugin is disconnected, or a different file is active, report `BLOCKED` and do not perform any write call. Never paste or print the MCP URL.

- [ ] **Step 2: Create the seven-page structure**

Use Penpot MCP write operations in small batches. Create or reuse exactly these pages, then read them back by name and ID:

```text
00 Foundations
01 Components
02 Desktop
03 Tablet
04 Mobile
05 States
06 Handoff
```

Expected: each name exists once; every returned ID opens in the active file.

- [ ] **Step 3: Create and apply semantic Token sets**

Create Penpot Token groups using these exact names and values:

```text
color.brand.100 #E6F3ED
color.brand.600 #287A60
color.brand.700 #1F634D
color.canvas #F5F8F6
color.surface #FFFFFF
color.text.primary #263B34
color.text.secondary #71847C
color.border #DCE7E1
color.danger #C43D4B
color.warning #A86816
color.success #287A60
color.focus #2F80ED

space.0 0px
space.1 4px
space.2 8px
space.3 12px
space.4 16px
space.5 20px
space.6 24px
space.8 32px
space.10 40px
space.12 48px

radius.sm 6px
radius.md 10px
radius.lg 16px
radius.pill 999px
sidebar.width 248px
topbar.height 64px
mobile-nav.height 64px
content.max-width 1200px
```

Create the seven approved typography styles and `Shadow/Surface`, `Shadow/Overlay`. Apply tokens to Foundation samples and read back bindings; raw brand colors must not be duplicated inside component definitions.

- [ ] **Step 4: Build component Variants from semantic tokens**

Create Button, IconButton, TextField, PasswordField, Checkbox, Badge, Avatar, Tooltip, Toast, Dialog, Drawer, Tabs, SidebarItem, Sidebar, MobileBottomNav, TopBar, PageHeader, EmptyState, Skeleton and AppShell.

Required Variant properties:

```text
Button hierarchy=primary|secondary|ghost|danger
Button size=sm|md|lg
Button state=default|hover|focus|disabled|loading
Button icon=none|leading|trailing
TextField state=default|hover|focus|filled|error|disabled
TextField label=on|off
TextField helper=none|help|error
SidebarItem state=default|hover|active|focus
SidebarItem collapsed=true|false
Toast tone=info|success|warning|error
Dialog size=sm|md|lg
AppShell viewport=desktop|tablet|mobile
```

Expected: variants are switchable, component copies remain linked, focus uses a visible 2px ring, and disabled/destructive states are not distinguished by color alone.

- [ ] **Step 5: Assemble the three responsive page families**

Use component copies and Penpot Flex/Grid Layout:

- Desktop `1440x1024`: 248px fixed sidebar, 64px topbar, 1200px content maximum.
- Tablet `1024x768`: 72px rail, on-demand drawer, two columns only when both remain at least 320px.
- Mobile `390x844`: one column, 64px bottom navigation, 44px minimum targets.

Create Login, Register, AppShell and Session expired boards. Navigation labels and the migration empty-state copy must exactly match the spec. Verify no detached copies, clipping or overflow.

- [ ] **Step 6: Build state and handoff boards**

Create loading, validation error, API error, unauthorized, session expired and migration empty states. Document keyboard order, drawer focus trap/return, Escape behavior, responsive breakpoints, CJK font fallback and `/legacy` action.

Expected: body text and controls meet WCAG AA; no status depends on color alone.

- [ ] **Step 7: Export the normalized Token snapshot and reference images**

Export Token JSON, normalize it to this top-level DTCG shape, and save it as `design/tokens/zhiyan.tokens.json`:

```json
{
  "color": { "brand": { "100": { "$type": "color", "$value": "#E6F3ED" } } },
  "space": { "1": { "$type": "dimension", "$value": { "value": 4, "unit": "px" } } },
  "radius": { "sm": { "$type": "dimension", "$value": { "value": 6, "unit": "px" } } }
}
```

The saved file must contain every Token from Step 3, not only the abbreviated example branches. Export the six named PNGs at their original board sizes. Inspect each image for overflow, alignment, contrast and missing fonts before accepting it.

- [ ] **Step 8: Record only real handoff values**

Write `docs/product-ui/penpot-handoff.md` with the real file URL (without `userToken`), seven page IDs, main component IDs, six reference-board IDs, target viewports, Token snapshot path and font fallback. Verify every URL/ID through a read call before marking it validated.

Run:

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m json.tool design/tokens/zhiyan.tokens.json | Out-Null
rg -n "userToken|figma.com|Figma Code Connect" design docs/product-ui
git diff --check
```

Expected: JSON validation exits `0`; secret/Figma search returns no matches; diff check exits `0`.

- [ ] **Step 9: Commit the Penpot design handoff**

```powershell
git add design/tokens/zhiyan.tokens.json docs/product-ui/penpot-handoff.md docs/product-ui/reference/penpot
git commit -m "docs: record Penpot product UI handoff"
```

---

### Task 2: 建立 DTCG Token 校验和确定性 CSS 生成

**Files:**
- Create: `scripts/design_tokens.mjs`
- Create: `tests/design/test_design_tokens.mjs`
- Create: `web/src/styles/tokens.css`
- Modify: `.gitignore`

**Interfaces:**
- Consumes: `design/tokens/zhiyan.tokens.json` from Task 1.
- Produces: `loadTokens(path)`, `renderCss(tokens)`, `writeCss(input, output)` and CLI `node scripts/design_tokens.mjs [--check] input output`.

- [ ] **Step 1: Write failing Node tests for flattening, units and drift**

Create tests using `node:test` and `node:assert/strict`:

```js
import test from "node:test";
import assert from "node:assert/strict";
import { renderCss } from "../../scripts/design_tokens.mjs";

test("renders DTCG paths as stable CSS variables", () => {
  const css = renderCss({
    color: { brand: { 600: { $type: "color", $value: "#287A60" } } },
    space: { 4: { $type: "dimension", $value: { value: 16, unit: "px" } } },
  });
  assert.match(css, /--color-brand-600: #287A60;/);
  assert.match(css, /--space-4: 16px;/);
  assert.ok(css.indexOf("--color-brand-600") < css.indexOf("--space-4"));
});
```

Also test duplicate flattened names, unsupported units/types, missing `$value`, `--check` success, and `--check` failure when output is stale.

- [ ] **Step 2: Run the tests and verify the missing-module failure**

```powershell
node --test tests/design/test_design_tokens.mjs
```

Expected: fail because `scripts/design_tokens.mjs` does not exist.

- [ ] **Step 3: Implement the minimal deterministic compiler**

The implementation must:

- recursively visit objects containing `$value`;
- derive `--` plus dot-path segments joined with `-`;
- render colors as strings and dimensions as `<value><unit>`;
- accept only `px`, `rem`, `%` for dimensions;
- sort CSS variable names lexicographically;
- emit `:root { ... }` with one trailing newline;
- make `--check` compare exact UTF-8 content without writing.

Implement `scripts/design_tokens.mjs` with this complete boundary:

```js
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname } from "node:path";
import { pathToFileURL } from "node:url";

function formatValue(path, token) {
  if (token.$type === "color" && typeof token.$value === "string") {
    return token.$value;
  }
  if (token.$type === "dimension") {
    const value = token.$value;
    if (
      value &&
      typeof value.value === "number" &&
      ["px", "rem", "%"].includes(value.unit)
    ) {
      return `${value.value}${value.unit}`;
    }
  }
  throw new Error(`Unsupported or invalid token: ${path}`);
}

export function flattenTokens(root) {
  const entries = [];
  const names = new Set();

  function visit(value, segments) {
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      throw new Error(`Invalid token group: ${segments.join(".") || "root"}`);
    }
    if (Object.hasOwn(value, "$value")) {
      const path = segments.join(".");
      const name = segments.join("-");
      if (names.has(name)) throw new Error(`Duplicate CSS token name: ${path}`);
      names.add(name);
      entries.push([name, formatValue(path, value)]);
      return;
    }
    for (const [key, child] of Object.entries(value)) {
      if (key.startsWith("$")) continue;
      visit(child, [...segments, ...key.split(".")]);
    }
  }

  visit(root, []);
  return entries;
}

export function renderCss(tokens) {
  const entries = flattenTokens(tokens).sort(([left], [right]) =>
    left.localeCompare(right),
  );
  const declarations = entries
    .map(([name, value]) => `  --${name}: ${value};`)
    .join("\n");
  return `:root {\n${declarations}\n}\n`;
}

export function loadTokens(inputPath) {
  return JSON.parse(readFileSync(inputPath, "utf8"));
}

export function writeCss(inputPath, outputPath) {
  mkdirSync(dirname(outputPath), { recursive: true });
  writeFileSync(outputPath, renderCss(loadTokens(inputPath)), "utf8");
}

export function checkCss(inputPath, outputPath) {
  return readFileSync(outputPath, "utf8") === renderCss(loadTokens(inputPath));
}

function main(args) {
  const check = args[0] === "--check";
  const [inputPath, outputPath] = check ? args.slice(1) : args;
  if (!inputPath || !outputPath) {
    throw new Error("Usage: node scripts/design_tokens.mjs [--check] input output");
  }
  if (check) {
    if (!checkCss(inputPath, outputPath)) {
      throw new Error(`Generated CSS is stale: ${outputPath}`);
    }
    return;
  }
  writeCss(inputPath, outputPath);
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main(process.argv.slice(2));
}
```

Error messages must name the invalid Token path but never print unrelated environment values.

- [ ] **Step 4: Generate and verify the committed CSS**

```powershell
node scripts/design_tokens.mjs design/tokens/zhiyan.tokens.json web/src/styles/tokens.css
node scripts/design_tokens.mjs --check design/tokens/zhiyan.tokens.json web/src/styles/tokens.css
node --test tests/design/test_design_tokens.mjs
git diff --check
```

Expected: compiler check and all Node tests pass; generated CSS contains all 30 approved tokens.

- [ ] **Step 5: Ignore only generated transient reports**

Add these entries without ignoring the committed Token/CSS/reference files:

```gitignore
web/dist/
web/playwright-report/
web/test-results/
```

- [ ] **Step 6: Commit the Token pipeline**

```powershell
git add design scripts/design_tokens.mjs tests/design/test_design_tokens.mjs web/src/styles/tokens.css .gitignore
git commit -m "build: compile Penpot tokens to CSS"
```

---

### Task 3: 建立 React 认证客户端、会话恢复和受保护路由

**Files:**
- Create: `web/package.json`, `web/package-lock.json`, `web/index.html`
- Create: `web/tsconfig.json`, `web/tsconfig.app.json`, `web/tsconfig.node.json`
- Create: `web/vite.config.ts`, `web/eslint.config.js`
- Create: `web/src/main.tsx`, `web/src/App.tsx`, `web/src/vite-env.d.ts`
- Create: `web/src/api/client.ts`, `web/src/api/client.test.ts`
- Create: `web/src/auth/AuthProvider.tsx`, `web/src/auth/AuthProvider.test.tsx`
- Create: `web/src/auth/ProtectedRoute.tsx`, `web/src/auth/ProtectedRoute.test.tsx`
- Create: `web/src/pages/LoginPage.tsx`, `web/src/pages/RegisterPage.tsx`
- Create: `web/src/styles/global.css`
- Create: `web/src/test/setup.ts`

**Interfaces:**
- Consumes: `/api/v1/auth/register|login|session|logout`, Task 2 `tokens.css`.
- Produces: `apiRequest<T>()`, `ApiError`, `AuthProvider`, `useAuth()`, `ProtectedRoute` for Task 4.

- [ ] **Step 1: Create the exact frontend dependency boundary**

Use React 19, React Router 7, TanStack Query 5, Vite 7, TypeScript 5, Vitest, jsdom, Testing Library, user-event and ESLint. Generate `package-lock.json` through npm; do not use floating Git dependencies.

Required scripts:

```json
{
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "typecheck": "tsc -b --pretty false",
    "test": "vitest run",
    "lint": "eslint ."
  }
}
```

- [ ] **Step 2: Write failing API-client tests**

Test `credentials: "same-origin"`, JSON parsing only when content exists, common error-envelope parsing, CSRF on POST/PUT/PATCH/DELETE only, and no CSRF on GET/HEAD.

```ts
await apiRequest("/api/v1/auth/logout", { method: "POST", csrfToken: "csrf-value" });
expect(fetchMock).toHaveBeenCalledWith(
  "/api/v1/auth/logout",
  expect.objectContaining({
    credentials: "same-origin",
    headers: expect.objectContaining({ "X-CSRF-Token": "csrf-value" }),
  }),
);
```

- [ ] **Step 3: Write failing auth-provider and protected-route tests**

Cover initial `/auth/session` loading, anonymous 401, successful session restore, login redirect to intended route, invalid-credentials banner, logout, and clearing in-memory state after a protected 401. Assert no token is written to localStorage/sessionStorage.

- [ ] **Step 4: Run tests and confirm missing implementation failures**

```powershell
Set-Location web
npm test
Set-Location ..
```

Expected: imports fail for absent client/provider/route modules.

- [ ] **Step 5: Implement the typed fetch boundary**

Use this public error type:

```ts
export type ApiErrorBody = {
  error: {
    code: string;
    message: string;
    retryable: boolean;
    field_errors: Record<string, string>;
  };
};

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly code: string,
    message: string,
    public readonly fieldErrors: Record<string, string> = {},
  ) {
    super(message);
  }
}
```

`apiRequest` never reads the HttpOnly cookie. It accepts an optional CSRF value from the provider and invokes an `onUnauthorized` callback for 401 responses.

- [ ] **Step 6: Implement in-memory authentication state**

`AuthProvider` owns only:

```ts
type AuthState =
  | { status: "loading" }
  | { status: "anonymous" }
  | { status: "authenticated"; username: string; csrfToken: string };
```

Use TanStack Query for request state without persistence. Successful login/register replace history with the intended protected location or `/overview`. Logout sends the current CSRF value, clears memory, then replaces history with `/login`.

- [ ] **Step 7: Implement accessible Login and Register pages**

Use semantic forms, associated labels, `aria-describedby`, visible field/server errors, disabled pending submit, brand/subtitle copy and links between `/login` and `/register`. Import only `tokens.css` and `global.css`; no raw approved brand colors in component styles.

- [ ] **Step 8: Run unit, type, lint and production build checks**

```powershell
Set-Location web
npm test
npm run typecheck
npm run lint
npm run build
Set-Location ..
node scripts/design_tokens.mjs --check design/tokens/zhiyan.tokens.json web/src/styles/tokens.css
```

Expected: all checks pass and `web/dist/index.html` exists.

- [ ] **Step 9: Commit the React authentication foundation**

```powershell
git add web design scripts tests/design
git commit -m "feat: add React session authentication"
```

---

### Task 4: 实现 Penpot 对齐的三档 AppShell 和迁移页

**Files:**
- Create: `web/src/layout/navigation.ts`
- Create: `web/src/layout/AppShell.tsx`, `web/src/layout/AppShell.test.tsx`
- Create: `web/src/components/Button/Button.tsx`
- Create: `web/src/components/TextField/TextField.tsx`
- Create: `web/src/components/Sidebar/Sidebar.tsx`
- Create: `web/src/components/MobileBottomNav/MobileBottomNav.tsx`
- Create: `web/src/components/TopBar/TopBar.tsx`
- Create: `web/src/components/MoreDrawer/MoreDrawer.tsx`
- Create: `web/src/components/MigrationEmptyState/MigrationEmptyState.tsx`
- Create: `web/src/pages/MigrationPage.tsx`
- Create: `web/src/styles/app-shell.css`
- Modify: `web/src/App.tsx`

**Interfaces:**
- Consumes: Task 1 Penpot board IDs/reference PNGs, Task 2 CSS variables, Task 3 auth/route contracts.
- Produces: semantic AppShell and reusable code components mapped in Task 6.

- [ ] **Step 1: Write failing centralized-navigation tests**

The only navigation array must contain these route/label pairs:

```ts
[
  ["/overview", "概览"],
  ["/documents", "文档库"],
  ["/qa", "智能问答"],
  ["/search", "文献检索"],
  ["/notes", "学习笔记"],
  ["/insights", "学习洞察"],
]
```

Test desktop six-item sidebar, mobile five-item bottom navigation, More drawer actions, `aria-current="page"`, skip link, keyboard focus and `/legacy` migration action.

- [ ] **Step 2: Run tests and verify component imports fail**

```powershell
Set-Location web
npm test -- AppShell.test.tsx
Set-Location ..
```

Expected: fail because AppShell/components do not exist.

- [ ] **Step 3: Implement shared semantic components**

Component APIs must align with Penpot Variants:

```ts
type ButtonProps = React.ButtonHTMLAttributes<HTMLButtonElement> & {
  hierarchy?: "primary" | "secondary" | "ghost" | "danger";
  size?: "sm" | "md" | "lg";
  loading?: boolean;
};

type TextFieldProps = React.InputHTMLAttributes<HTMLInputElement> & {
  label: string;
  helperText?: string;
  error?: string;
};
```

Do not encode hover/focus as React props; CSS pseudo-classes implement browser states while matching Penpot visual variants.

- [ ] **Step 4: Implement the responsive layout contract**

Use mobile default CSS plus these exact breakpoints:

```css
@media (min-width: 768px) { /* tablet rail and drawer */ }
@media (min-width: 1200px) { /* 248px persistent desktop sidebar */ }
```

Use CSS variables for widths/heights/spacing. Desktop shows Sidebar; tablet shows rail and drawer; mobile shows MobileBottomNav and MoreDrawer. `main` receives focus from the skip link.

- [ ] **Step 5: Add explicit migration pages**

Routes `/overview`, `/documents`, `/qa`, `/search`, `/notes`, `/insights` each display a real heading and the exact text:

```text
该能力正在迁移到新版界面，可暂时前往旧版使用。
```

The secondary action links to `/legacy`; do not render fabricated document, QA, search, note or insight data.

- [ ] **Step 6: Verify three viewport layouts locally**

Run unit/type/build checks, then use browser responsive mode at `1440x1024`, `1024x768`, `390x844`. Compare against Task 1 reference images for navigation mode, spacing, typography, clipping and focus order. Record deliberate rendering differences in `penpot-handoff.md`, not in component comments.

- [ ] **Step 7: Commit the responsive AppShell**

```powershell
git add web/src web/index.html docs/product-ui/penpot-handoff.md
git commit -m "feat: add responsive Penpot application shell"
```

---

### Task 5: 将 React、FastAPI 和 Gradio `/legacy` 合并为单一生产运行时

**Files:**
- Create: `server.py`
- Create: `tests/api/test_mounts.py`
- Modify: `api/app.py`
- Modify: `ui/gradio_app.py`
- Modify: `web/vite.config.ts`
- Modify: `Dockerfile`
- Modify: `compose.yaml`
- Modify: `deploy/entrypoint.sh`
- Modify: `deploy/healthcheck.py`
- Modify: `deploy/.env.example`
- Modify: `tests/deploy/test_image_contract.py`
- Modify: `tests/deploy/test_compose_contract.py`

**Interfaces:**
- Consumes: existing `ApplicationServices`, FastAPI auth app and `web/dist`.
- Produces: `create_application(services=None, legacy_app=None, dist_dir=None) -> FastAPI` and `create_gradio_app(services=None) -> gr.Blocks`.

**Runtime contract:**

```text
GET /healthz         FastAPI health JSON
/api/v1/*            FastAPI JSON API
/legacy/*            mounted Gradio sharing ApplicationServices
/*                   React SPA with history fallback
```

- [ ] **Step 1: Write failing mount and lifecycle tests**

Use a temporary fake `dist` and fake legacy ASGI app. Test `/healthz`, auth JSON, `/legacy/`, `/overview` history fallback, real asset serving, JSON 404 for unknown `/api/*`, and exactly one services start/stop around the entire client lifespan.

- [ ] **Step 2: Refactor Gradio into a factory without changing handlers**

Expose:

```python
def create_gradio_app(
    services: ApplicationServices | None = None,
) -> gr.Blocks:
    """Return the existing component tree bound to shared services."""
```

Keep `demo = create_gradio_app()` for current tests/local legacy launch. The factory must not start workers; FastAPI lifespan owns lifecycle.

- [ ] **Step 3: Assemble routes in safe order**

Create one FastAPI application, include API routes first, mount Gradio at `/legacy`, mount `/assets`, then add a GET-only HTML fallback. The fallback must reject API/legacy prefixes and requests that do not accept HTML. Never call `demo.launch()` from `server.py`.

- [ ] **Step 4: Configure Vite development proxy**

Proxy `/api` and `/legacy` to `http://127.0.0.1:7860`. Development uses:

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m uvicorn server:app --host 127.0.0.1 --port 7860 --workers 1
Set-Location web; npm run dev
```

- [ ] **Step 5: Convert Docker to a multi-stage build**

Use Node 22 to run `npm ci` and `npm run build`, then copy `/web/dist` into the existing non-root Python 3.11 image. Final entrypoint:

```sh
exec python -m uvicorn server:app \
  --host "${APP_HOST:-0.0.0.0}" \
  --port "${APP_PORT:-7860}" \
  --workers 1
```

Health check probes `/healthz`. Compose removes obsolete Gradio host/port settings, preserves `PDF_ASSISTANT_DATA_DIR`, and maps the configured `APP_PORT`.

- [ ] **Step 6: Run mount, API, UI and deployment tests**

```powershell
D:\python_self_agent\venv\Scripts\python.exe -m pytest tests/api tests/ui tests/deploy/test_image_contract.py tests/deploy/test_compose_contract.py -q --basetemp=.pytest-tmp-penpot-unified
Set-Location web
npm test
npm run typecheck
npm run build
Set-Location ..
```

Split Python tests into complete bounded groups if the per-command cap is exceeded. Report Docker CLI absence explicitly rather than claiming an image build passed.

- [ ] **Step 7: Run a real local smoke test**

Start one Uvicorn process on a non-default port and disposable `PDF_ASSISTANT_DATA_DIR`; poll `/healthz`, then request `/`, `/overview`, `/legacy/`, and `/api/v1/auth/session`. Expected: 200, 200, 200 or documented legacy-root redirect, and 401. Stop only the process started by this step.

- [ ] **Step 8: Commit the unified runtime**

```powershell
git add server.py api/app.py ui/gradio_app.py web/vite.config.ts Dockerfile compose.yaml deploy tests/api/test_mounts.py tests/deploy
git commit -m "feat: serve React and legacy UI from FastAPI"
```

---

### Task 6: 建立 Penpot 组件映射和三视口浏览器验收

**Files:**
- Create: `docs/product-ui/penpot-component-map.json`
- Create: `docs/product-ui/penpot-component-map.schema.json`
- Create: `tests/design/test_penpot_component_map.mjs`
- Create: `web/playwright.config.ts`
- Create: `web/e2e/auth-shell.spec.ts`
- Create: `web/e2e/accessibility.spec.ts`
- Create: `web/e2e/visual.spec.ts`
- Create: `web/e2e/visual.spec.ts-snapshots/*`
- Modify: `docs/product-ui/penpot-handoff.md`
- Modify: `web/package.json`, `web/package-lock.json`

**Interfaces:**
- Consumes: actual Penpot IDs from Task 1 and React components from Task 4.
- Produces: validated component mapping plus user-flow, axe and visual acceptance suites.

- [ ] **Step 1: Add mapping schema and failing validation tests**

The JSON Schema requires:

```json
{
  "fileUrl": "string without userToken",
  "components": [
    {
      "penpotId": "non-empty unique string",
      "penpotName": "non-empty string",
      "codeFile": "existing repository-relative path",
      "exportName": "non-empty string",
      "variants": { "Penpot property": ["allowed values"] },
      "verified": true
    }
  ]
}
```

Test unique IDs, existing code files, no Figma paths, no secrets, `verified=true`, and required mappings for Button, TextField, AppShell, Sidebar and MobileBottomNav.

- [ ] **Step 2: Read actual Penpot components and write the mapping**

Reconnect Remote MCP, read the active file name, then read each component by its actual ID from `penpot-handoff.md`. Map Penpot Variant names to the existing React props without inventing props. Re-read every ID after writing the JSON.

- [ ] **Step 3: Configure Playwright and axe**

Install `@playwright/test` and `@axe-core/playwright`. Configure one Chromium version and these projects:

```ts
projects: [
  { name: "desktop", use: { viewport: { width: 1440, height: 1024 } } },
  { name: "tablet", use: { viewport: { width: 1024, height: 768 } } },
  { name: "mobile", use: { viewport: { width: 390, height: 844 } } },
]
```

Disable animations for screenshots and honor reduced motion.

- [ ] **Step 4: Test the complete real authentication flow**

Use a disposable data directory. Register a unique user, reach `/overview`, navigate every shell destination, open the mobile More drawer, log out with CSRF, and confirm a protected URL returns to `/login`. The server fixture must stop and delete only its disposable data root.

- [ ] **Step 5: Add accessibility tests**

Run axe on Login, Register, authenticated AppShell and mobile More drawer in all three projects. Serious and critical violations must be zero. Also assert focus return after closing drawers/dialogs and visible keyboard focus.

- [ ] **Step 6: Compare Penpot references before accepting snapshots**

Export/read the six Task 1 boards again through Penpot MCP and confirm IDs are unchanged. Compare browser screenshots at the same dimensions with `docs/product-ui/reference/penpot/*.png`. Fix code tokens/layout for unexplained differences; do not silently edit the approved design source to match buggy code.

- [ ] **Step 7: Create stable browser visual baselines**

Cover desktop/mobile login, desktop/tablet/mobile shell, mobile More drawer, validation error, server error and session-expired states. Mask only genuinely dynamic values; do not mask layout regions.

- [ ] **Step 8: Run mapping, unit, build and E2E checks**

```powershell
node --test tests/design/test_design_tokens.mjs tests/design/test_penpot_component_map.mjs
Set-Location web
npm test
npm run typecheck
npm run build
npx playwright test
Set-Location ..
```

Expected: mapping validation passes; all three browser projects pass; screenshots change only after deliberate review.

- [ ] **Step 9: Commit the verified Penpot bridge**

```powershell
git add docs/product-ui tests/design web/playwright.config.ts web/e2e web/package.json web/package-lock.json
git commit -m "test: verify Penpot authentication shell across viewports"
```

---

### Task 7: 完成全量回归、文档和发布门槛

**Files:**
- Modify: `README.md`
- Create: `docs/product-ui/README.md`
- Create: `tests/deploy/test_product_ui_readme.py`

**Interfaces:**
- Consumes: all preceding tasks.
- Produces: repeatable development/deployment documentation and final phase report.

- [ ] **Step 1: Add a failing documentation contract test**

Assert root README contains `知研`, Penpot handoff link, Token build/check command, React development command, unified Uvicorn command, `/legacy`, `/healthz`, single-worker constraint and link to `docs/product-ui/README.md`.

- [ ] **Step 2: Document the supported workflow**

`docs/product-ui/README.md` must describe product routes, first-slice scope, Penpot connection without exposing secrets, Penpot handoff, DTCG ownership, component mapping validation, local frontend/backend commands, unified production command, Docker build, Cookie/CSRF behavior, `APP_COOKIE_SECURE`, `/legacy` rollback, three viewports, screenshot policy and next slices: documents, QA, search, notes, insights.

- [ ] **Step 3: Run the complete Python suite in the repository venv**

```powershell
New-Item -ItemType Directory -Force .runtime\pytest-penpot-ui | Out-Null
$env:TEMP=(Resolve-Path '.runtime\pytest-penpot-ui').Path
$env:TMP=$env:TEMP
D:\python_self_agent\venv\Scripts\python.exe -m pytest -q --basetemp=.runtime\pytest-penpot-ui\base
```

Record exact pass/skip totals. If the suite exceeds the available window, split by top-level test directories/files so every collected test is accounted for; do not reuse historical totals.

- [ ] **Step 4: Run complete design and frontend checks**

```powershell
node scripts/design_tokens.mjs --check design/tokens/zhiyan.tokens.json web/src/styles/tokens.css
node --test tests/design/test_design_tokens.mjs tests/design/test_penpot_component_map.mjs
Set-Location web
npm ci
npm test
npm run typecheck
npm run lint
npm run build
npx playwright test
Set-Location ..
```

- [ ] **Step 5: Run unified-server smoke checks**

Use a temporary data root, one Uvicorn worker and a non-default port. Verify health, SPA history fallback, registration, session restore, CSRF rejection, CSRF logout and `/legacy`. Confirm the worker pool starts once and no test process remains.

- [ ] **Step 6: Verify Penpot handoff one final time read-only**

Reconnect the active Penpot file, confirm file name, seven page IDs, mapped component IDs and six board IDs. Compare stored reference image metadata with the current exported boards. Do not perform design writes during this release gate.

- [ ] **Step 7: Inspect staging safety**

```powershell
git diff --check
git status --short
git diff --stat
git diff --name-only --cached
rg -n "userToken|design\.penpot\.app/.+userToken|figma\.com" --glob '!docs/superpowers/**' .
```

Expected: no secret, runtime database, uploaded document, generated `web/dist`, Playwright report/trace, `.env`, or unrelated task-packet edit is staged.

- [ ] **Step 8: Commit documentation closure**

```powershell
git add README.md docs/product-ui/README.md tests/deploy/test_product_ui_readme.py
git commit -m "docs: document Penpot product UI workflow"
```

- [ ] **Step 9: Produce the phase completion report**

Report Penpot file/page/component links, Token and mapping validation, changed architecture boundaries, Python/frontend/Playwright/axe/visual results, commit list, push status, TestClient warning status, and the known limitation that product feature pages remain explicit migration states pointing to `/legacy`.

---

## Plan Self-Review

### Spec coverage

| Approved requirement | Plan task |
|---|---|
| Penpot Cloud + Remote MCP | 1, 6, 7 |
| Penpot as sole design source | Global constraints, 1 |
| Seven-page file structure | 1 |
| DTCG Token and CSS generation | 1, 2 |
| Public components and Variants | 1, 4 |
| Desktop/tablet/mobile | 1, 4, 6 |
| React auth/session shell | 3, 4 |
| Shared FastAPI/Gradio runtime | 5 |
| Component mapping replacing Code Connect | 6 |
| Playwright, axe and visual comparison | 6, 7 |
| MCP secret safety and recovery | Global constraints, 1, 7 |
| Existing data isolation preserved | Global constraints, 5, 7 |

### Type and interface consistency

- Task 2 produces the exact CSS imported by Tasks 3–4.
- Task 3 produces `apiRequest`, `AuthProvider`, `useAuth` and `ProtectedRoute` consumed by Task 4.
- Task 4 component props are the only code APIs recorded in Task 6 mappings.
- Task 5 reuses the existing `ApplicationServices` and FastAPI authentication contracts; it does not create a second registry or worker pool.
- Task 6 consumes only real IDs recorded by Task 1 and validates referenced code paths.

### Scope and safety

- Completed backend tasks are prerequisites, not reimplementation work.
- No task contains Figma Code Connect or `.figma.tsx` deliverables.
- Dynamic Penpot IDs are execution-time evidence and are never guessed.
- Each implementation task has a failing-test gate, focused verification and isolated commit.
- External Penpot writes stop cleanly when MCP state is ambiguous.

**Plan complete and saved to `docs/superpowers/plans/2026-08-05-penpot-product-ui-foundation-auth.md`.**
