# 知研产品 UI 收尾修复 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `subagent-driven-development` to implement this plan task-by-task. Execute the four tasks serially in the same isolated worktree; do not overlap their file ownership or external Penpot/Docker state.

**Goal:** 关闭产品 UI 分支剩余的五类问题：npm moderate advisory、Starlette `TestClient` 弃用警告、无效的“保持登录状态”承诺、两项 Penpot 设计源清理，以及尚未完成的 Docker Linux 交付验证，并留下可重复执行的验收记录。

**Architecture:** 保持现有 React → FastAPI `/api/v1` → 单一 `ApplicationServices` 单向边界。认证仍使用浏览器会话 Cookie、内存会话和 12 小时滑动空闲过期；不新增持久登录。Penpot 继续是唯一设计源，代码视觉基线只在重新导出的 Penpot 参考图通过人工对照后更新。Docker 验证使用独立 Compose project、独立端口和工作树内数据根，禁止影响现有 7860 部署。

**Tech Stack:** Penpot 2.17.1 Remote MCP、React 19、TypeScript 5、Vite 7、Vitest、Playwright、Ajv 8.20.0、FastAPI、Starlette、httpx/httpx2、pytest、Docker Desktop Linux Engine、Docker Compose。

---

## Global constraints and task graph

- Before implementation, read `PROJECT_KNOWLEDGE.md`; current code/tests/runtime win over historical notes.
- Approved design source: `docs/superpowers/specs/2026-08-09-product-ui-closure-design.md`.
- Execute serially: `Task 1 → Task 2 → Task 3 → Task 4`.
- Preserve Cookie flags, CSRF rules, 12-hour sliding in-memory expiry, single-worker runtime, per-user UUID storage, `document_id`, citations, RAG, Memory, reports, imports, and `/legacy/`.
- Do not introduce `localStorage`, `sessionStorage`, persistent refresh tokens, a second Gradio process, or deep LLM smoke calls.
- Penpot writes require a fresh active-file/page read. If the connector times out, the file/page differs, or a target ID no longer resolves, stop that batch without guessing.
- Do not update visual snapshots until the corresponding Penpot boards have been freshly exported and visually inspected.
- Docker cleanup may target only the exact unique Compose project and a resolved data root under this worktree.

### File responsibility map

| Task | Owned paths/state |
|---|---|
| 1 | `requirements-dev.txt`, root development-install docs, `web/package*.json`, dependency contract test, project venv packages |
| 2 | Penpot file, Penpot handoff/reference exports, Penpot handoff contract test |
| 3 | Login React/CSS/unit/E2E/visual contract and Login browser snapshots |
| 4 | Closure report, product-UI workflow docs/test, isolated Docker project/data, final regression evidence |

---

### Task 1: Close Ajv and TestClient dependency findings

**Files:**

- Create: `requirements-dev.txt`
- Create: `tests/deploy/test_dependency_contract.py`
- Modify: `README.md`
- Modify: `tests/api/test_mounts.py` only at the existing cold-import subprocess timeout
- Modify: `web/package.json`
- Modify: `web/package-lock.json`
- Modify: `tests/api/test_mounts.py`

**Interfaces:**

- Production Python containers continue installing only `requirements.txt`.
- Local/test setup installs `requirements-dev.txt`, which includes `-r requirements.txt` and pins `httpx2==2.9.1`.
- Frontend keeps a direct exact dev dependency `ajv==8.20.0`; `ajv-formats` remains compatible.

- [ ] **Step 1: Add the failing dependency contract**

Create `tests/deploy/test_dependency_contract.py` with focused assertions that:

```python
def test_development_requirements_add_httpx2_without_shipping_it_in_docker():
    dev = (ROOT / "requirements-dev.txt").read_text(encoding="utf-8")
    assert "-r requirements.txt" in dev
    assert "httpx2==2.9.1" in dev
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY requirements.txt /app/requirements.txt" in dockerfile
    assert "requirements-dev.txt" not in dockerfile


def test_frontend_pins_fixed_ajv_directly():
    package = json.loads((ROOT / "web/package.json").read_text(encoding="utf-8"))
    assert package["devDependencies"]["ajv"] == "8.20.0"


def test_readme_uses_development_requirements_for_local_setup():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "-r requirements-dev.txt" in readme
```

Run:

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest tests/deploy/test_dependency_contract.py -q
```

Expected RED: `requirements-dev.txt` is absent and direct Ajv is `8.17.1`.

- [ ] **Step 2: Separate runtime and development Python dependencies**

Create:

```text
-r requirements.txt
httpx2==2.9.1
```

Update only the local/test install command in `README.md` to use `requirements-dev.txt`. Do not add `httpx2` to `requirements.txt` or the runtime Docker layer.

- [ ] **Step 3: Upgrade direct Ajv exactly and refresh the lockfile**

```powershell
Set-Location web
npm install --save-dev --save-exact ajv@8.20.0
npm ci
Set-Location ..
```

Do not force-upgrade ESLint's nested Ajv 6, React Router, FastAPI, Starlette, or `httpx<1`.

- [ ] **Step 4: Install and prove the Python test adapter**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pip install -r requirements-dev.txt
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pip check
& 'D:\python_self_agent\venv\Scripts\python.exe' -c "import warnings; from starlette.exceptions import StarletteDeprecationWarning; warnings.simplefilter('error', StarletteDeprecationWarning); from fastapi.testclient import TestClient; print(TestClient.__module__)"
```

If `httpx2` is installed but importing `TestClient` still emits the same warning, stop with a reality-conflict report; do not suppress or filter the warning.

If the unchanged late-binding lifecycle subprocess exceeds its historical 45-second ceiling after the adapter install, raise only that test-only hard timeout to 90 seconds and retain every lifecycle/assertion line. Do not change application timeouts.

- [ ] **Step 5: Run Task 1 GREEN gates**

Before the gates, change only the isolated late-binding subprocess timeout in
`tests/api/test_mounts.py` from 45 to 90 seconds. Measurement on the target
Windows worktree showed `import server` at about 46.4 seconds while entering
and leaving the lifespan took under 0.2 seconds; the larger budget preserves
the assertion while removing machine-level cold-import flakiness.

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest tests/deploy/test_dependency_contract.py tests/api -q -p no:cacheprovider -W error::starlette.exceptions.StarletteDeprecationWarning --basetemp=.runtime/pytest-closure-deps

Set-Location web
npm ls ajv ajv-formats
npm audit
npm audit --omit=dev
npm test
npm run typecheck
npm run lint
npm run build
Set-Location ..
```

Expected: dependency contract and API tests pass; `pip check` passes; both npm audits report zero vulnerabilities; frontend gates pass.

- [ ] **Step 6: Commit Task 1**

```powershell
git add requirements-dev.txt README.md tests/api/test_mounts.py web/package.json web/package-lock.json tests/deploy/test_dependency_contract.py
git diff --cached --check
git commit -m "chore: close UI dependency advisories"
```

---

### Task 2: Clean the Penpot source and publish Login references

**Files/state:**

- External modify: Penpot file `知研 · 智能文档学习助手` (`3be9e5e1-190f-8090-8008-6ff3f3dcd54c`)
- Create: `tests/design/test_penpot_handoff.mjs`
- Modify: `docs/product-ui/penpot-handoff.md`
- Modify: `docs/product-ui/README.md`
- Replace: `docs/product-ui/reference/penpot/desktop-login.png`
- Create: `docs/product-ui/reference/penpot/tablet-login.png`
- Replace: `docs/product-ui/reference/penpot/mobile-login.png`

**Known immutable IDs:**

```text
00 Foundations page  3be9e5e1-190f-8090-8008-6ff3f3dcd54d
01 Components page   9b1e7a6b-703c-8060-8008-7071c343b8c2
02 Desktop page      9b1e7a6b-703c-8060-8008-7071c3463d87
03 Tablet page       9b1e7a6b-703c-8060-8008-7071c9876902
04 Mobile page       9b1e7a6b-703c-8060-8008-7071c9888df9
Blank Board          0f745b42-1a51-801c-8008-6ff39f5b8841
TextField main       9b1e7a6b-703c-8060-8008-70743ee69ea9
PasswordField main   9b1e7a6b-703c-8060-8008-7074e2350798
Desktop / Login      9b1e7a6b-703c-8060-8008-70761a57accd
Mobile / Login       9b1e7a6b-703c-8060-8008-707701065fab
```

- [ ] **Step 1: Fresh-read all mutable targets before any write**

Read file ID/name/revision, seven pages, blank board structure, TextField and PasswordField main-component structures, and every Desktop/Tablet/Mobile Login/Register board. Discover and record:

- Tablet Login board ID and `1024 × 768` bounds;
- each Login remember-row ID and parent;
- TextField internal Input ID;
- PasswordField internal Input, spacer, and 44×44 eye IDs;
- current `layoutChild.horizontalSizing`, component linkage, and instance override state.

Write the exact readback into the Task 2 report/handoff before mutation. If any known ID is missing or points to a different semantic object, stop without writing.

- [ ] **Step 2: Add a failing repository handoff contract**

Create `tests/design/test_penpot_handoff.mjs` that requires:

- the handoff says the blank board ID was removed;
- no `remember rows read back` claim remains;
- `/legacy/` is canonical;
- desktop/tablet/mobile Login rows name real Penpot board IDs and PNG paths;
- the three PNG files exist and decode to `1440×1024`, `1024×768`, and `390×844` respectively;
- the handoff records fill sizing for the exact TextField/PasswordField internal IDs discovered in Step 1.

Run:

```powershell
node --test tests/design/test_penpot_handoff.mjs
```

Expected RED: tablet Login reference is absent and the handoff still documents remember rows.

- [ ] **Step 3: Delete only the unused blank board**

Activate `00 Foundations`, re-read the blank board as `100×100`, zero children, and parent under that page. Delete only `0f745b42-1a51-801c-8008-6ff39f5b8841`. Read back that this ID is absent and `Board / Foundation System` still exists unchanged.

- [ ] **Step 4: Correct internal component fill behavior**

Activate `01 Components` and re-read the exact child IDs from Step 1.

- Set the TextField internal Input child `layoutChild.horizontalSizing = "fill"`.
- Set the PasswordField internal Input and spacer children to horizontal `fill`.
- Keep the eye control right-aligned, `44×44`, fixed-size, and linked.
- Do not hard-code either main component or page instance to 400 px.

Read back main component and representative instances after each small mutation batch.

- [ ] **Step 5: Remove the Login remember row in all three viewports**

For Desktop, Tablet, and Mobile Login only, remove the exact remember-row objects discovered in Step 1. Do not remove Register content or alter password visibility controls. Reflow with existing flex gap/token spacing; preserve headings, username/password fields, submit button, account link, security copy, and component linkage.

- [ ] **Step 6: Validate all affected boards and instances**

Require:

- Desktop, Tablet, and Mobile Login boards keep target viewport dimensions;
- all Login/Register field copies remain linked;
- zero broken component links, text overflow, or actual-bounds overflow;
- input surfaces fill their parent form width;
- Desktop Login input and submit edges align at `x=900..1300`;
- inputs remain 44 px high, submit 48 px, eye 44×44;
- Mobile Login keeps Hero 188 px and Form 656 px without clipping;
- Register boards remain semantically complete.

- [ ] **Step 7: Export and visually inspect three authoritative Login boards**

Export original-size PNGs directly from the live Penpot boards to the three repository paths. Open every PNG and inspect hierarchy, edge alignment, wrapping, spacing, contrast, glyphs, and clipping. Do not create placeholders or copy browser snapshots into the Penpot reference directory.

- [ ] **Step 8: Update handoff and run GREEN gates**

Record file revision/date, exact changed/removed IDs, fill readbacks, three Login board IDs/dimensions, zero-overflow/link results, and canonical `/legacy/` wording.

```powershell
node --test tests/design/test_penpot_handoff.mjs tests/design/test_penpot_component_map.mjs
node scripts/design_tokens.mjs --check design/tokens/zhiyan.tokens.json web/src/styles/tokens.css
git diff --check
```

- [ ] **Step 9: Commit Task 2**

```powershell
git add tests/design/test_penpot_handoff.mjs docs/product-ui/README.md docs/product-ui/penpot-handoff.md docs/product-ui/reference/penpot/desktop-login.png docs/product-ui/reference/penpot/tablet-login.png docs/product-ui/reference/penpot/mobile-login.png
git diff --cached --check
git commit -m "fix: clean Penpot login source"
```

---

### Task 3: Remove the inert React control and synchronize three visual baselines

**Files:**

- Modify: `web/src/pages/LoginPage.tsx`
- Modify: `web/src/styles/global.css`
- Modify: `web/src/auth/AuthProvider.test.tsx`
- Modify: `web/e2e/accessibility.spec.ts`
- Modify: `web/e2e/visual.spec.ts`
- Modify: `web/tests/visual-acceptance-contract.test.ts`
- Replace: `web/e2e/visual.spec.ts-snapshots/login-desktop.png`
- Create: `web/e2e/visual.spec.ts-snapshots/login-tablet.png`
- Replace: `web/e2e/visual.spec.ts-snapshots/login-mobile.png`

**Behavioral boundary:** Login still submits exactly `{ username, password }`; authentication state, Cookie, CSRF, storage, restore, expiry, routing, and logout behavior are unchanged.

- [ ] **Step 1: Write focused RED assertions**

In `AuthProvider.test.tsx`, replace the checked-checkbox assertion with absence and retain an exact request-body assertion:

```tsx
expect(screen.queryByRole("checkbox", { name: "保持登录状态" })).not.toBeInTheDocument();
expect(JSON.parse(String(loginRequest?.[1]?.body))).toEqual({
  username: "reader",
  password: "correct horse battery",
});
```

In `accessibility.spec.ts`, assert the checkbox count is zero. In `visual-acceptance-contract.test.ts`, require exactly 16 baselines, adding `login-tablet.png`. Remove the tablet Login skip in `visual.spec.ts`.

```powershell
Set-Location web
npm test -- src/auth/AuthProvider.test.tsx tests/visual-acceptance-contract.test.ts
Set-Location ..
```

Expected RED: old Login still renders the checkbox and there are only 15 baselines.

- [ ] **Step 2: Remove only the inert control and CSS**

Delete `.remember-control` JSX from `LoginPage.tsx` and its dedicated CSS rules from `global.css`. Do not add replacement persistence copy or behavior.

- [ ] **Step 3: Run code-level GREEN gates**

```powershell
Set-Location web
npm test -- src/auth/AuthProvider.test.tsx tests/visual-acceptance-contract.test.ts
npm run typecheck
npm run lint
npm run build
Set-Location ..
```

- [ ] **Step 4: Verify accessibility before accepting visuals**

Run the existing three-project Playwright accessibility spec against the real unified server. Require zero serious/critical axe violations, no remember checkbox, visible focus, and unchanged 44×44 password-visibility controls.

```powershell
Set-Location web
npx playwright test e2e/accessibility.spec.ts
Set-Location ..
```

- [ ] **Step 5: Update only the three Login snapshots after manual Penpot comparison**

First run Login visual tests without update and confirm the expected intentional diffs. Compare desktop/tablet/mobile browser screenshots with Task 2's Penpot exports. Only then run:

```powershell
Set-Location web
npx playwright test e2e/visual.spec.ts --grep "login baseline" --update-snapshots
npx playwright test e2e/visual.spec.ts --grep "login baseline"
Set-Location ..
```

Open all six images side by side. Accept only differences attributable to browser font antialiasing; correct unexplained layout, wrapping, or clipping differences in code or Penpot source before proceeding.

- [ ] **Step 6: Commit Task 3**

```powershell
git add web/src/pages/LoginPage.tsx web/src/styles/global.css web/src/auth/AuthProvider.test.tsx web/e2e/accessibility.spec.ts web/e2e/visual.spec.ts web/tests/visual-acceptance-contract.test.ts web/e2e/visual.spec.ts-snapshots/login-desktop.png web/e2e/visual.spec.ts-snapshots/login-tablet.png web/e2e/visual.spec.ts-snapshots/login-mobile.png
git diff --cached --check
git commit -m "fix: remove inert login persistence control"
```

---

### Task 4: Verify isolated Docker delivery and record full closure evidence

**Files/state:**

- Create: `docs/product-ui/closure-report-2026-08-09.md`
- Modify: `docs/product-ui/README.md`
- Modify: `tests/deploy/test_product_ui_readme.py`
- Ephemeral only: `.runtime/closure-docker/`, Compose project `zhiyan-closure-20260809`

- [ ] **Step 1: Add the failing durable-report contract**

Extend `tests/deploy/test_product_ui_readme.py` to require a discoverable `closure-report-2026-08-09.md` link and the isolated verification invariants: unique project name, `127.0.0.1:17860`, `--workers 1`, shallow smoke, and no deep smoke.

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest tests/deploy/test_product_ui_readme.py -q
```

Expected RED: the report/link does not exist.

- [ ] **Step 2: Snapshot and protect the existing deployment**

Capture Docker context/client/server/Compose versions and existing `python_self_agent-app-1` / `python_self_agent-qdrant-1` IDs, images, status, health, and port bindings. Confirm the existing app owns 7860. Store evidence in memory/report notes, not in tracked runtime JSON.

- [ ] **Step 3: Create isolated ignored runtime configuration**

Create `.runtime/closure-docker/deploy.env` from `deploy/.env.example`, with at least:

```dotenv
APP_BIND_ADDRESS=127.0.0.1
APP_HOST=0.0.0.0
APP_PORT=17860
APP_UID=1000
APP_GID=1000
DEPLOY_DATA_ROOT=<absolute path under this worktree>/.runtime/closure-docker/data
LLM_API_KEY=closure-smoke-placeholder
LLM_BASE_URL=https://example.invalid/v1
LLM_MODEL_ID=closure-smoke-placeholder
RAG_BACKEND=qdrant
QDRANT_URL=http://qdrant:6333
```

Set environment variables for every Compose invocation:

```powershell
$env:COMPOSE_PROJECT_NAME = 'zhiyan-closure-20260809'
$env:DEPLOY_ENV_FILE = (Resolve-Path '.runtime\closure-docker\deploy.env').Path
```

Validate the resolved data root is a descendant of the current worktree before creating or later deleting it.

- [ ] **Step 4: Build and start only the isolated project**

```powershell
docker compose --env-file $env:DEPLOY_ENV_FILE -p $env:COMPOSE_PROJECT_NAME build app qdrant
docker compose --env-file $env:DEPLOY_ENV_FILE -p $env:COMPOSE_PROJECT_NAME up -d app qdrant
docker compose --env-file $env:DEPLOY_ENV_FILE -p $env:COMPOSE_PROJECT_NAME ps
```

Require both services running/healthy. Verify the app image was built from the current worktree commit and only `127.0.0.1:17860` is exposed.

- [ ] **Step 5: Prove the container delivery contract**

Run read-only/runtime checks proving:

- app process runs as non-root UID/GID;
- `/healthz` returns `{"status":"ok"}`;
- SPA root and a history route such as `/documents` return HTML 200;
- `/legacy` returns canonical 307 to `/legacy/`, and `/legacy/`/`/legacy/config` load;
- Qdrant `/readyz` succeeds from the app network;
- `/app/data` is writable and the probe is removed;
- `hello_agents.__file__ == /app/hello_agents/__init__.py`;
- exactly one Uvicorn worker is configured;
- `deploy/smoke_test.py --env-file ...` exits 0 without `--deep`.

Do not use real LLM credentials and do not run deep smoke.

- [ ] **Step 6: Clean up the isolated project safely**

In a `finally` path:

```powershell
docker compose --env-file $env:DEPLOY_ENV_FILE -p $env:COMPOSE_PROJECT_NAME down --remove-orphans
```

Confirm no containers/networks with the unique project label remain. Resolve and boundary-check `.runtime/closure-docker/data`, then remove only that isolated directory and env file. Do not use an unscoped `docker compose down`, `--volumes`, or target the workspace root.

- [ ] **Step 7: Prove the original 7860 stack is unchanged**

Repeat the exact before snapshot and compare IDs/images/health/port bindings. Require the original app and Qdrant containers still healthy and 7860 still reachable.

- [ ] **Step 8: Run full product-UI regression**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q -W error::starlette.testclient.StarletteDeprecationWarning --basetemp=.runtime/pytest-product-ui-closure

node scripts/design_tokens.mjs --check design/tokens/zhiyan.tokens.json web/src/styles/tokens.css
node --test tests/design/test_design_tokens.mjs tests/design/test_penpot_component_map.mjs tests/design/test_penpot_handoff.mjs

Set-Location web
npm ci
npm audit
npm audit --omit=dev
npm test
npm run typecheck
npm run lint
npm run build
npx playwright test
Set-Location ..
```

Also run the real single-worker unified-server smoke used by the existing acceptance suite and record `/healthz`, SPA, register/session, CSRF rejection/logout, `/legacy/`, lifecycle start/stop, process, and data-root cleanup results.

- [ ] **Step 9: Write the durable closure report**

`docs/product-ui/closure-report-2026-08-09.md` must record:

- base/final commits and dirty status;
- Ajv/httpx2 resolved versions, npm audit outputs, `pip check`, and warning-as-error evidence;
- Penpot file/revision/page/component/child/board IDs, deleted board evidence, fill readbacks, reference export hashes/dimensions, linkage/overflow results;
- React unit/type/lint/build/axe/three-viewport visual results;
- Docker context/version/project/port/data root, build/image/container health, non-root UID, endpoint/Qdrant/volume/import/shallow-smoke evidence;
- before/after proof that the 7860 deployment was untouched;
- cleanup evidence;
- full Python pass/skip/warning totals, design gates, frontend totals, Playwright totals, unified smoke, secret/Figma/raw-color/runtime-artifact/diff scans;
- explicit remaining risks (`None` only if independently supported).

Link the report from `docs/product-ui/README.md` and document prerequisites plus exact command order.

- [ ] **Step 10: Final review and commit**

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest tests/deploy/test_product_ui_readme.py tests/deploy/test_dependency_contract.py -q
git diff --check
git status --short
git add docs/product-ui/README.md docs/product-ui/closure-report-2026-08-09.md tests/deploy/test_product_ui_readme.py
git diff --cached --check
git commit -m "docs: record product UI closure verification"
```

After commit, perform the mandatory independent final integration review against the complete diff from `ef93550` to the final commit. Any Critical/Important finding becomes a corrective packet and must be fixed/re-reviewed before completion.
