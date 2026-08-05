# 知研产品化 UI 基础与认证垂直切片实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付“知研 · 智能文档学习助手”的首个可运行产品化垂直切片：建立 Figma 设计系统与三档响应式登录/应用壳，抽出 React 与 Gradio 共用的应用启动层，通过 FastAPI `/api/v1` 提供 Cookie + CSRF 认证，并让 React 成为默认入口、现有 Gradio 暂留在 `/legacy`。

**Architecture:** 保持 `UI/API -> Session/Application Services -> Assistant -> Tool -> Memory/RAG/Storage` 单向依赖。新增 `app/bootstrap.py` 作为唯一服务组合根；FastAPI lifespan 只启动一次后台导入 Worker，并同时挂载 React 静态资源与共享同一组服务的 Gradio。React 使用 React Router、TanStack Query 和类型化 API 客户端；认证令牌仅放在 HttpOnly Cookie，CSRF 令牌由会话接口返回并只保存在内存。第一期业务页面除“登录/注册/退出/会话恢复”外只交付真实导航壳和明确的“功能迁移中”空状态，不伪造未接通的数据。

**Tech Stack:** Python 3.12（本地 `venv`）/ Python 3.11（容器）、FastAPI、Uvicorn、Gradio 6.19、React 19、TypeScript 5、Vite 7、React Router 7、TanStack Query 5、Vitest、Testing Library、Playwright、axe-core、Figma Dev Mode/Code Connect、pytest。

## Global Constraints

- 实施前完整读取 `PROJECT_KNOWLEDGE.md`，当前代码、测试和运行行为优先于历史说明。
- 不改变 `document_id` 隔离、用户 UUID 数据目录、来源页码、引用、RAG、Memory、报告、批量导入任务和存储契约。
- 单进程、单 Uvicorn Worker；FastAPI 与 `/legacy` 只能共享一份 SessionRegistry、RuntimeRegistry、ImportTaskRepository、ImportWorkerPool 和 ImportTaskService。
- 不把会话令牌写入 `localStorage`、`sessionStorage`、DOM、日志或 JSON 响应；Cookie 必须为 HttpOnly、SameSite=Lax，生产环境可通过配置启用 Secure。
- 所有改变状态的 `/api/v1` 请求都必须验证 `X-CSRF-Token`，注册、登录和健康检查除外。
- API 错误形状固定为 `{"error":{"code":"example_code","message":"用户可读信息","retryable":false,"field_errors":{}}}`。
- 未认证访问受保护路由时跳转到 `/login`；会话过期时清除前端内存态并返回登录页，不渲染旧用户数据。
- 三档验收视口固定为桌面 `1440x1024`、平板 `1024x768`、手机 `390x844`。
- 设计 Token 是 Figma 与代码的共同契约；色值、间距和圆角不得在页面组件内散落硬编码。
- Figma 文件和节点 ID 由工具实际返回后写入 `docs/product-ui/figma-handoff.md`；不得编造 URL、Key 或节点 ID。
- 使用 Figma 写工具前必须加载相应 Figma skill：新建文件前加载 `figma-create-new-file`，任何写入前加载 `figma-use`，组件库同时加载 `figma-generate-library`，页面组装同时加载 `figma-generate-design`，读取设计上下文前加载 `figma-design-to-code`，Code Connect 映射时加载 `figma-code-connect`。
- Python 验证使用 `.\venv\Scripts\python.exe`，pytest 使用仓库内 `--basetemp`，避免 Windows 系统临时目录权限问题。
- 每个任务先写失败测试，再做最小实现；每个任务独立提交，只 stage 任务列出的文件，绝不带入当前 GraphRAG task-packet 的用户改动。
- 本计划仅完成第一期基础与认证垂直切片；文档库、问答、检索、笔记和洞察分别由后续垂直切片计划实现。

---

### Task 1: 在 Figma 建立 Foundations、公共组件和认证/应用壳三档页面

**Files:**
- Create: `docs/product-ui/figma-handoff.md`
- External create/update: Figma 文件 `知研 · 智能文档学习助手`

**Figma structure:**
- `00 Foundations`: colors, typography, spacing, radius, shadow, breakpoints, grids, accessibility notes.
- `01 Components`: Button, IconButton, TextField, PasswordField, Checkbox, Badge, Avatar, Tooltip, Toast, Dialog, Drawer, Tabs, SidebarItem, Sidebar, MobileBottomNav, TopBar, PageHeader, EmptyState, Skeleton, AppShell.
- `02 Desktop`: Login / Register / AppShell / Session expired.
- `03 Tablet`: Login / AppShell / collapsed navigation drawer.
- `04 Mobile`: Login / AppShell / More drawer.
- `05 States`: loading, validation error, API error, unauthorized, session expired, empty migration state.
- `06 Handoff`: component usage, responsive rules, keyboard focus, Code Connect status.

- [ ] **Step 1: Load required skills and create the real design file**

Load `figma-create-new-file` before the creation call. If Figma reports more than one accessible team/workspace, pause only for that required workspace choice; otherwise create the design file immediately. Capture the returned file URL/key exactly.

Expected: a writable Figma Design file named `知研 · 智能文档学习助手` exists; no local handoff document is written with a guessed URL.

- [ ] **Step 2: Build semantic variables before components**

Load `figma-use` and `figma-generate-library`. Create these collections and modes:

```text
Collection: Color / Mode: Light
color.brand.100      #E6F3ED
color.brand.600      #287A60
color.brand.700      #1F634D
color.canvas         #F5F8F6
color.surface        #FFFFFF
color.text.primary   #263B34
color.text.secondary #71847C
color.border         #DCE7E1
color.danger         #C43D4B
color.warning        #A86816
color.success        #287A60
color.focus          #2F80ED

Collection: Dimension
space.0 0; space.1 4; space.2 8; space.3 12; space.4 16
space.5 20; space.6 24; space.8 32; space.10 40; space.12 48
radius.sm 6; radius.md 10; radius.lg 16; radius.pill 999
sidebar.width 248; topbar.height 64; mobile-nav.height 64
content.max-width 1200
```

Use Inter for Latin/numerals and the platform CJK sans-serif fallback in handoff guidance. Define type styles `Display/32/40/Semibold`, `Heading/24/32/Semibold`, `Title/18/26/Semibold`, `Body/16/24/Regular`, `Body/14/22/Regular`, `Label/14/20/Medium`, `Caption/12/18/Regular`.

Expected: component colors and dimensions are variable-bound; inspection shows no duplicated raw brand colors inside component variants.

- [ ] **Step 3: Build component sets with complete states**

Use auto-layout, semantic variable bindings and variant properties. Required variants:

```text
Button: hierarchy=primary|secondary|ghost|danger,
        size=sm|md|lg, state=default|hover|focus|disabled|loading,
        icon=none|leading|trailing
TextField: state=default|hover|focus|filled|error|disabled,
           label=on|off, helper=none|help|error
SidebarItem: state=default|hover|active|focus, collapsed=true|false
Toast: tone=info|success|warning|error
Dialog: size=sm|md|lg
AppShell: viewport=desktop|tablet|mobile
```

Focus state must use a visible 2 px focus ring; body text and controls meet WCAG AA contrast. Icons use a single consistent outline family and 20/24 px sizes.

Expected: all variants can be switched through component properties; destructive and disabled states are visually distinguishable without color alone.

- [ ] **Step 4: Assemble responsive screens from instances**

Load `figma-generate-design` alongside `figma-use`. Use component instances, not detached copies.

Desktop (`1440x1024`): fixed 248 px sidebar, 64 px top bar, centered content max width 1200 px. Sidebar labels are 概览、文档库、智能问答、文献检索、学习笔记、学习洞察. Header shows brand `知研` and subtitle `智能文档学习助手`.

Tablet (`1024x768`): compact 72 px rail plus on-demand drawer; page content remains two-column only when each column is at least 320 px.

Mobile (`390x844`): one-column content and bottom navigation 概览、文档、问答、检索、更多; “更多” drawer contains 学习笔记、学习洞察、账户、退出登录.

Create login and registration cards with username/password fields, inline validation, pending state and server-error banner. AppShell pages use a real page heading and a consistent empty state: `该能力正在迁移到新版界面，可暂时前往旧版使用。` with a secondary action to `/legacy`.

Expected: no frame clips at the three target sizes; navigation selection, focus order and account actions are documented.

- [ ] **Step 5: Visually verify and record actual node IDs**

Take screenshots of the Desktop, Tablet and Mobile login/app-shell frames. Inspect at original scale for overflow, misalignment, contrast and detached instances. Fix all visible issues before handoff.

Write the local handoff using actual returned values:

```markdown
# 知研 Figma Handoff

- File: [知研 · 智能文档学习助手](<actual Figma URL>)
- File key: `<actual key>`
- Foundations node: `<actual node id>`
- Components node: `<actual node id>`
- Desktop login node: `<actual node id>`
- Desktop shell node: `<actual node id>`
- Tablet shell node: `<actual node id>`
- Mobile shell node: `<actual node id>`
- Session-expired state node: `<actual node id>`

## Contract

- Target viewports: 1440x1024, 1024x768, 390x844
- Source tokens: `web/src/styles/tokens.css`
- Code Connect: added in Task 7
```

The angle-bracketed values above are execution-time substitutions, not text to commit. Reject the step if any recorded node cannot be opened.

- [ ] **Step 6: Review and commit only the handoff record**

Run:

```powershell
git diff --check
git add docs/product-ui/figma-handoff.md
git commit -m "docs: record product UI Figma handoff"
```

Expected: one commit; the handoff URL and every node ID resolve to the newly created file.

---

### Task 2: 抽出唯一共享 ApplicationServices 启动层

**Files:**
- Create: `app/bootstrap.py`
- Create: `tests/test_app_bootstrap.py`
- Modify: `ui/gradio_app.py:1-110,1530-1552`

**Interfaces:**
- `ApplicationServices.create(data_root: Path | None = None) -> ApplicationServices`
- `ApplicationServices.start() -> None`
- `ApplicationServices.stop() -> None`
- `get_application_services() -> ApplicationServices`
- `reset_application_services_for_tests() -> None`
- Repeated `start()`/`stop()` calls are idempotent.

- [ ] **Step 1: Write failing lifecycle and singleton tests**

```python
# tests/test_app_bootstrap.py
from pathlib import Path

import app.bootstrap as bootstrap


class FakeWorkerPool:
    def __init__(self):
        self.starts = 0
        self.stops = 0

    def start(self):
        self.starts += 1

    def stop(self):
        self.stops += 1


def test_application_services_lifecycle_is_idempotent():
    services = object.__new__(bootstrap.ApplicationServices)
    services.import_worker_pool = FakeWorkerPool()
    services._started = False

    services.start()
    services.start()
    services.stop()
    services.stop()

    assert services.import_worker_pool.starts == 1
    assert services.import_worker_pool.stops == 1


def test_get_application_services_reuses_singleton(monkeypatch, tmp_path: Path):
    created = []
    sentinel = object()
    monkeypatch.setattr(
        bootstrap.ApplicationServices,
        "create",
        classmethod(lambda cls, data_root=None: created.append(data_root) or sentinel),
    )
    bootstrap.reset_application_services_for_tests()

    assert bootstrap.get_application_services() is sentinel
    assert bootstrap.get_application_services() is sentinel
    assert len(created) == 1
```

- [ ] **Step 2: Run focused tests and confirm the missing module failure**

Run:

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_app_bootstrap.py -q --basetemp=.pytest-tmp-bootstrap
```

Expected: collection fails with `ModuleNotFoundError: No module named 'app.bootstrap'`.

- [ ] **Step 3: Implement the composition root using existing constructors**

Move the construction currently performed by `ui.gradio_app.initialize_app_services()` into this dataclass. Use the current repository constructors and initialization order verbatim; do not redesign services while moving them.

```python
# app/bootstrap.py
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock

import os

from app.database import initialize_database
from app.import_repository import ImportTaskRepository
from app.import_service import ImportTaskService
from app.import_worker import ImportWorkerPool
from app.migration import LegacyMigrationService
from app.session import SessionRegistry
from app.storage import UserStorage


PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class ApplicationServices:
    data_root: Path
    db_path: Path
    storage: UserStorage
    session_registry: SessionRegistry
    legacy_migration: LegacyMigrationService
    import_repository: ImportTaskRepository
    import_worker_pool: ImportWorkerPool
    import_service: ImportTaskService
    _started: bool = field(default=False, init=False, repr=False)

    @classmethod
    def create(cls, data_root: Path | None = None) -> "ApplicationServices":
        configured_root = os.getenv("PDF_ASSISTANT_DATA_DIR")
        resolved_root = Path(
            data_root or configured_root or PROJECT_ROOT / "data"
        ).resolve()
        database_path = resolved_root / "app.db"
        initialize_database(database_path)
        storage = UserStorage(resolved_root)
        session_registry = SessionRegistry(
            db_path=database_path,
            storage=storage,
        )
        legacy_migration = LegacyMigrationService(
            database_path,
            storage,
            PROJECT_ROOT,
        )
        import_repository = ImportTaskRepository(database_path)
        import_worker_pool = ImportWorkerPool(
            import_repository,
            session_registry.runtime_registry,
            storage,
        )
        import_service = ImportTaskService(
            session_registry,
            import_repository,
            storage,
            import_worker_pool,
        )
        session_registry.runtime_registry.set_import_task_service(import_service)
        return cls(
            data_root=resolved_root,
            db_path=database_path,
            storage=storage,
            session_registry=session_registry,
            legacy_migration=legacy_migration,
            import_repository=import_repository,
            import_worker_pool=import_worker_pool,
            import_service=import_service,
        )

    def start(self) -> None:
        if self._started:
            return
        self.import_worker_pool.start()
        self._started = True

    def stop(self) -> None:
        if not self._started:
            return
        self.import_worker_pool.stop()
        self._started = False
```

Guard singleton construction with a module `RLock`. `reset_application_services_for_tests()` must stop an existing started instance before clearing it.

- [ ] **Step 4: Delegate Gradio startup without breaking handler monkeypatching**

Keep module-level aliases in `ui/gradio_app.py` because existing tests monkeypatch them:

```python
services = get_application_services()
session_registry = services.session_registry
legacy_migration = services.legacy_migration
import_repository = services.import_repository
import_worker_pool = services.import_worker_pool
import_service = services.import_service
```

Change `initialize_app_services()` to return/refresh aliases from `get_application_services()`. Change `start_import_workers()` and final cleanup to call `services.start()`/`services.stop()`. Do not touch the handler bodies or the Gradio component tree in this task.

- [ ] **Step 5: Run bootstrap, session, import and authenticated-handler regressions**

```powershell
.\venv\Scripts\python.exe -m pytest `
  tests/test_app_bootstrap.py `
  tests/test_session_registry.py `
  tests/test_import_service.py `
  tests/test_import_worker.py `
  tests/ui/test_authenticated_handlers.py `
  -q --basetemp=.pytest-tmp-bootstrap
```

Expected: all selected tests pass; a diagnostic assertion confirms two calls to initialization still return the same registry and pool.

- [ ] **Step 6: Commit the composition-root refactor**

```powershell
git diff --check
git add app/bootstrap.py tests/test_app_bootstrap.py ui/gradio_app.py
git commit -m "refactor: share application service lifecycle"
```

---

### Task 3: 为现有会话补充 CSRF 契约

**Files:**
- Modify: `app/session.py`
- Modify: `tests/test_session_registry.py`

**Interfaces:**
- `UserSession.csrf_token: str`
- `InvalidCsrfTokenError(ValueError)`
- `SessionRegistry.validate_csrf(token: str | None, csrf_token: str | None) -> UserSession`

- [ ] **Step 1: Add failing CSRF tests**

```python
from app.session import InvalidCsrfTokenError


def test_created_session_has_independent_csrf_token(registry):
    token = registry.register("reader", "correct-horse-battery-staple")
    session = registry.get_session(token)

    assert session.csrf_token
    assert session.csrf_token != token
    assert len(session.csrf_token) >= 32


def test_validate_csrf_accepts_only_current_session_token(registry):
    token = registry.register("reader", "correct-horse-battery-staple")
    session = registry.get_session(token)

    assert registry.validate_csrf(token, session.csrf_token) is session
    for value in (None, "", "wrong"):
        with pytest.raises(InvalidCsrfTokenError):
            registry.validate_csrf(token, value)
```

Also test that another session’s CSRF token is rejected and logout invalidates both session and CSRF validation.

- [ ] **Step 2: Run the focused test and confirm it fails**

```powershell
.\venv\Scripts\python.exe -m pytest tests/test_session_registry.py -q --basetemp=.pytest-tmp-csrf
```

Expected: `UserSession` has no `csrf_token` and `validate_csrf` is missing.

- [ ] **Step 3: Implement constant-time CSRF validation**

```python
class InvalidCsrfTokenError(ValueError):
    """Raised when a state-changing request has no valid CSRF token."""


@dataclass
class UserSession:
    token: str
    csrf_token: str
    # existing fields remain unchanged


def validate_csrf(
    self, token: str | None, csrf_token: str | None
) -> UserSession:
    session = self.get_session(token)
    if not csrf_token or not secrets.compare_digest(session.csrf_token, csrf_token):
        raise InvalidCsrfTokenError("Invalid CSRF token")
    return session
```

Generate `csrf_token=secrets.token_urlsafe(32)` in `_create_session`. Never log either token.

- [ ] **Step 4: Run session and authenticated UI regressions**

```powershell
.\venv\Scripts\python.exe -m pytest `
  tests/test_session_registry.py tests/ui/test_authenticated_handlers.py `
  -q --basetemp=.pytest-tmp-csrf
```

Expected: all tests pass; existing Gradio calls remain unaffected because CSRF is an HTTP adapter concern.

- [ ] **Step 5: Commit the session contract**

```powershell
git add app/session.py tests/test_session_registry.py
git commit -m "feat: add CSRF validation to user sessions"
```

---

### Task 4: 增加 FastAPI 应用、统一错误与认证路由

**Files:**
- Modify: `requirements.txt`
- Create: `api/__init__.py`
- Create: `api/config.py`
- Create: `api/errors.py`
- Create: `api/dependencies.py`
- Create: `api/schemas/__init__.py`
- Create: `api/schemas/auth.py`
- Create: `api/routes/__init__.py`
- Create: `api/routes/auth.py`
- Create: `api/app.py`
- Create: `tests/api/__init__.py`
- Create: `tests/api/test_auth_routes.py`
- Create: `tests/api/test_app_lifecycle.py`

**HTTP contract:**

| Method | Path | Auth | CSRF | Result |
|---|---|---|---|---|
| `GET` | `/healthz` | no | no | `{"status":"ok"}` |
| `POST` | `/api/v1/auth/register` | no | no | set cookie + session DTO |
| `POST` | `/api/v1/auth/login` | no | no | set cookie + session DTO |
| `GET` | `/api/v1/auth/session` | cookie | no | session DTO |
| `POST` | `/api/v1/auth/logout` | cookie | yes | `204` + clear cookie |

Session DTO is `{"username":"reader","csrf_token":"returned-csrf-token"}`. It does not include session token, password hash, user UUID, runtime path or assistant data.

- [ ] **Step 1: Add compatible runtime dependencies**

Append explicit major ranges:

```text
fastapi>=0.115,<1
uvicorn[standard]>=0.30,<1
python-multipart>=0.0.20,<1
```

Install through the project venv and run `pip check`. Do not loosen existing Gradio or Qdrant pins.

- [ ] **Step 2: Write failing endpoint tests with an injected fake registry**

```python
# tests/api/test_auth_routes.py
from fastapi.testclient import TestClient

from api.app import create_api_app


def test_login_sets_http_only_cookie_and_returns_csrf(fake_services):
    with TestClient(create_api_app(services=fake_services)) as client:
        response = client.post(
            "/api/v1/auth/login",
            json={"username": "reader", "password": "strong-password"},
        )

    assert response.status_code == 200
    assert response.json() == {
        "username": "reader",
        "csrf_token": fake_services.session.csrf_token,
    }
    cookie = response.headers["set-cookie"]
    assert "zhiyan_session=" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert fake_services.session.token not in response.text


def test_logout_requires_matching_csrf(fake_services):
    app = create_api_app(services=fake_services)
    with TestClient(app) as client:
        client.cookies.set("zhiyan_session", fake_services.session.token)
        rejected = client.post("/api/v1/auth/logout")
        accepted = client.post(
            "/api/v1/auth/logout",
            headers={"X-CSRF-Token": fake_services.session.csrf_token},
        )

    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "csrf_invalid"
    assert accepted.status_code == 204
```

Cover register success, duplicate username `409`, bad credentials `401`, missing/expired session `401`, validation error `422` in the common envelope, logout cookie expiry, and health endpoint.

- [ ] **Step 3: Verify failure before implementation**

```powershell
.\venv\Scripts\python.exe -m pytest tests/api -q --basetemp=.pytest-tmp-api
```

Expected: collection fails because `api.app` does not exist.

- [ ] **Step 4: Implement configuration and error envelope**

```python
# api/config.py
from dataclasses import dataclass
import os


@dataclass(frozen=True)
class ApiConfig:
    cookie_name: str = "zhiyan_session"
    cookie_secure: bool = False
    cookie_samesite: str = "lax"

    @classmethod
    def from_environment(cls) -> "ApiConfig":
        return cls(cookie_secure=os.getenv("APP_COOKIE_SECURE", "false").lower() == "true")
```

```python
# api/errors.py
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


def error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    field_errors: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable,
                "field_errors": field_errors or {},
            }
        },
    )
```

Add explicit exception handlers for `AuthError`, `InvalidSessionError`, `InvalidCsrfTokenError`, `RequestValidationError` and unexpected exceptions. Unexpected exceptions log an opaque request ID and return a generic message; no token, password or filesystem path is logged.

- [ ] **Step 5: Implement auth dependencies and routes**

Use `request.app.state.services` to obtain the shared registry. The cookie dependency only returns a `UserSession`; mutation routes call `validate_csrf` with the header. Password fields use Pydantic `SecretStr` so repr/logging cannot expose them.

```python
# api/schemas/auth.py
from pydantic import BaseModel, Field, SecretStr


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    password: SecretStr = Field(min_length=8, max_length=256)


class SessionResponse(BaseModel):
    username: str
    csrf_token: str
```

Set the session cookie with:

```python
response.set_cookie(
    key=config.cookie_name,
    value=token,
    httponly=True,
    secure=config.cookie_secure,
    samesite="lax",
    path="/",
)
```

Delete it with the same path and SameSite policy on logout.

- [ ] **Step 6: Implement dependency-injectable app creation and lifespan**

```python
# api/app.py
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.bootstrap import ApplicationServices, get_application_services


def create_api_app(services: ApplicationServices | None = None) -> FastAPI:
    resolved = services or get_application_services()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.services = resolved
        resolved.start()
        try:
            yield
        finally:
            resolved.stop()

    app = FastAPI(title="知研 API", version="1.0.0", lifespan=lifespan)
    # register handlers, /healthz and /api/v1/auth router
    return app


app = create_api_app()
```

`tests/api/test_app_lifecycle.py` must assert two HTTP calls start the service once, and TestClient shutdown stops it once.

- [ ] **Step 7: Run API, session and bootstrap suites**

```powershell
.\venv\Scripts\python.exe -m pytest `
  tests/api tests/test_app_bootstrap.py tests/test_session_registry.py `
  -q --basetemp=.pytest-tmp-api
.\venv\Scripts\python.exe -m pip check
```

Expected: all tests pass and `pip check` prints `No broken requirements found.`

- [ ] **Step 8: Commit the HTTP authentication slice**

```powershell
git add requirements.txt api tests/api
git commit -m "feat: add FastAPI session authentication"
```

---

### Task 5: 创建 React 工程、设计 Token、认证状态和响应式应用壳

**Files:**
- Create: `web/package.json`
- Create: `web/package-lock.json`
- Create: `web/tsconfig.json`
- Create: `web/tsconfig.app.json`
- Create: `web/tsconfig.node.json`
- Create: `web/vite.config.ts`
- Create: `web/index.html`
- Create: `web/src/main.tsx`
- Create: `web/src/app/App.tsx`
- Create: `web/src/app/router.tsx`
- Create: `web/src/api/client.ts`
- Create: `web/src/auth/AuthProvider.tsx`
- Create: `web/src/auth/authApi.ts`
- Create: `web/src/components/Button/Button.tsx`
- Create: `web/src/components/TextField/TextField.tsx`
- Create: `web/src/components/AppShell/AppShell.tsx`
- Create: `web/src/components/AppShell/Sidebar.tsx`
- Create: `web/src/components/AppShell/MobileBottomNav.tsx`
- Create: `web/src/components/Feedback/FullPageStatus.tsx`
- Create: `web/src/pages/LoginPage.tsx`
- Create: `web/src/pages/MigrationPlaceholderPage.tsx`
- Create: `web/src/styles/tokens.css`
- Create: `web/src/styles/global.css`
- Create: `web/src/test/setup.ts`
- Create: `web/src/**/*.test.tsx` for the components/pages above

**Routes:**

```text
/login
/overview
/documents
/qa
/search
/notes
/insights?tab=stats|reports
```

- [ ] **Step 1: Scaffold with pinned major versions and deterministic scripts**

`package.json` must provide:

```json
{
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "lint": "eslint . --max-warnings=0",
    "test": "vitest run",
    "test:watch": "vitest",
    "typecheck": "tsc -b --pretty false",
    "e2e": "playwright test"
  }
}
```

Install React/React DOM 19, React Router 7, TanStack Query 5, Vite 7 and TypeScript 5 using npm; commit the resolved `package-lock.json`. Add Vitest, jsdom, Testing Library, user-event and ESLint as dev dependencies. Do not use floating Git dependencies.

- [ ] **Step 2: Write failing API-client and auth-provider tests**

```tsx
it("adds CSRF only to mutating requests", async () => {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(null, { status: 204 }),
  );
  vi.stubGlobal("fetch", fetchMock);

  await apiRequest("/api/v1/auth/logout", {
    method: "POST",
    csrfToken: "csrf-value",
  });

  expect(fetchMock).toHaveBeenCalledWith(
    "/api/v1/auth/logout",
    expect.objectContaining({
      credentials: "same-origin",
      headers: expect.objectContaining({ "X-CSRF-Token": "csrf-value" }),
    }),
  );
});
```

Also test the common error envelope, 401 session reset, initial `/auth/session` loading screen, successful login redirect, invalid credentials banner and logout.

- [ ] **Step 3: Write failing app-shell navigation tests**

At desktop width, assert six sidebar destinations and brand text. At mobile width, assert five bottom navigation destinations and that “更多” opens notes/insights/account actions. Test `aria-current="page"`, keyboard focus and the `/legacy` link on migration placeholder pages.

- [ ] **Step 4: Verify the tests fail before components exist**

```powershell
Set-Location web
npm test
Set-Location ..
```

Expected: imports fail for the absent client/provider/components.

- [ ] **Step 5: Implement the token contract first**

```css
/* web/src/styles/tokens.css */
:root {
  --color-brand-100: #e6f3ed;
  --color-brand-600: #287a60;
  --color-brand-700: #1f634d;
  --color-canvas: #f5f8f6;
  --color-surface: #ffffff;
  --color-text-primary: #263b34;
  --color-text-secondary: #71847c;
  --color-border: #dce7e1;
  --color-danger: #c43d4b;
  --color-focus: #2f80ed;
  --space-1: 0.25rem;
  --space-2: 0.5rem;
  --space-3: 0.75rem;
  --space-4: 1rem;
  --space-5: 1.25rem;
  --space-6: 1.5rem;
  --space-8: 2rem;
  --space-10: 2.5rem;
  --space-12: 3rem;
  --radius-sm: 0.375rem;
  --radius-md: 0.625rem;
  --radius-lg: 1rem;
  --shadow-card: 0 8px 24px rgb(38 59 52 / 8%);
  --sidebar-width: 15.5rem;
  --topbar-height: 4rem;
  --mobile-nav-height: 4rem;
  --content-max-width: 75rem;
}
```

Global CSS must set `box-sizing`, readable CJK system font fallbacks, canvas/surface colors, a `:focus-visible` ring, `prefers-reduced-motion`, and 44 px minimum mobile hit targets.

- [ ] **Step 6: Implement one typed fetch boundary**

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

`apiRequest` always uses `credentials: "same-origin"`, parses JSON only when present, attaches CSRF for POST/PUT/PATCH/DELETE, and throws `ApiError`. It never reads or writes the session cookie.

- [ ] **Step 7: Implement auth state and protected routing**

`AuthProvider` owns only `{status, username, csrfToken}` in memory. On mount, query `/api/v1/auth/session`; while pending show the branded full-page skeleton; on 401 expose anonymous state. Use TanStack Query for request state but prevent sensitive session DTO persistence.

Protected routes redirect anonymous users to `/login` with the intended location. Successful login replaces history with that location or `/overview`. A 401 from any protected request invalidates the session query and transitions to anonymous.

- [ ] **Step 8: Implement the three-layout AppShell from Figma**

Use these CSS breakpoints consistently:

```css
/* Mobile default: bottom nav */
@media (min-width: 768px) { /* Tablet: compact rail/drawer */ }
@media (min-width: 1200px) { /* Desktop: 248 px persistent sidebar */ }
```

Components must expose semantic HTML (`nav`, `main`, headings, buttons), visible skip link and correct current-page state. Navigation labels and route mapping are centralized in one typed array so desktop, tablet and mobile cannot drift.

- [ ] **Step 9: Run unit, type and production build checks**

```powershell
Set-Location web
npm test
npm run typecheck
npm run build
Set-Location ..
```

Expected: all tests pass; typecheck exits `0`; `web/dist/index.html` and hashed assets are produced without warnings treated as errors.

- [ ] **Step 10: Commit the first React vertical slice**

```powershell
git add web
git commit -m "feat: add responsive React authentication shell"
```

---

### Task 6: 将 React、FastAPI 与 Gradio `/legacy` 合并为单一生产进程

**Files:**
- Create: `server.py`
- Modify: `api/app.py`
- Modify: `ui/gradio_app.py`
- Modify: `web/vite.config.ts`
- Modify: `Dockerfile`
- Modify: `compose.yaml`
- Modify: `deploy/entrypoint.sh`
- Modify: `deploy/healthcheck.py`
- Modify: `deploy/.env.example`
- Create: `tests/api/test_mounts.py`
- Modify: `tests/deploy/test_image_contract.py`
- Modify: `tests/deploy/test_compose_contract.py`

**Runtime contract:**

```text
GET /healthz         FastAPI health
/api/v1/*            FastAPI JSON API
/legacy/*            mounted Gradio using shared ApplicationServices
/*                   React SPA with history fallback
```

- [ ] **Step 1: Write failing mount and lifecycle tests**

Test that `/healthz` returns JSON, `/api/v1/auth/session` remains API JSON, `/legacy/` resolves to Gradio HTML, `/overview` serves React `index.html`, and services start/stop exactly once for the whole application.

The test app may mount a small fake legacy ASGI app and a temporary fake `dist` directory; one separate integration test imports the real Gradio factory to catch mount incompatibility without launching a socket.

- [ ] **Step 2: Refactor Gradio module to expose a factory**

Add:

```python
def create_gradio_app(services: ApplicationServices | None = None) -> gr.Blocks:
    """Return the existing component tree bound to shared services."""
```

Keep `demo = create_gradio_app()` for backwards-compatible tests and local legacy launch. The factory must not start workers. Worker lifecycle belongs only to FastAPI lifespan in the unified server.

- [ ] **Step 3: Mount Gradio before the SPA fallback**

Use Gradio’s supported FastAPI mounting helper from installed Gradio 6.19.0. Mount `/legacy` after API routes and before the catch-all/static SPA handler. Never call `demo.launch()` from `server.py`.

For SPA delivery, serve actual files under `/assets` and return `index.html` only for non-API GET routes that accept HTML. Missing `/api/*` routes must stay JSON 404, not React HTML.

- [ ] **Step 4: Configure local frontend proxy**

`web/vite.config.ts` proxies `/api` and `/legacy` to `http://127.0.0.1:7860`; frontend dev remains on Vite’s port. Document two local processes only for development:

```powershell
.\venv\Scripts\python.exe -m uvicorn server:app --host 127.0.0.1 --port 7860 --workers 1
Set-Location web; npm run dev
```

- [ ] **Step 5: Convert Dockerfile to a multi-stage build**

```dockerfile
FROM node:22-bookworm-slim AS web-build
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.11-slim-bookworm
# preserve current non-root user and Python installation
COPY --from=web-build /web/dist /app/web/dist
```

Change entrypoint’s final command to:

```sh
exec python -m uvicorn server:app \
  --host "${APP_HOST:-0.0.0.0}" \
  --port "${APP_PORT:-7860}" \
  --workers 1
```

Health check probes `/healthz`; Compose exposes the same `APP_PORT` and removes obsolete `GRADIO_SERVER_NAME`/`GRADIO_SERVER_PORT` application settings. Keep `GRADIO_ROOT_PATH=/legacy` only if the installed mounting API requires it.

- [ ] **Step 6: Run mount, API, UI and deployment contract tests**

```powershell
.\venv\Scripts\python.exe -m pytest `
  tests/api tests/ui tests/deploy/test_image_contract.py `
  tests/deploy/test_compose_contract.py `
  -q --basetemp=.pytest-tmp-unified
Set-Location web
npm test
npm run build
Set-Location ..
```

Expected: all selected tests pass. On a Linux Docker host additionally run `docker compose config` and image build; on this Windows host report Docker CLI absence explicitly if still unavailable.

- [ ] **Step 7: Run a real local smoke check**

Start one Uvicorn process, poll `/healthz`, then request `/`, `/overview`, `/legacy/` and `/api/v1/auth/session`. Expected statuses are 200, 200, 200/redirect-to-legacy-root, and 401 respectively. Stop only the process started by this step.

- [ ] **Step 8: Commit the unified runtime**

```powershell
git add server.py api/app.py ui/gradio_app.py web/vite.config.ts `
  Dockerfile compose.yaml deploy/entrypoint.sh deploy/healthcheck.py `
  deploy/.env.example tests/api/test_mounts.py `
  tests/deploy/test_image_contract.py tests/deploy/test_compose_contract.py
git commit -m "feat: serve React and legacy UI from FastAPI"
```

---

### Task 7: 建立 Figma Code Connect 和三视口浏览器验收

**Files:**
- Create: `web/playwright.config.ts`
- Create: `web/e2e/auth-shell.spec.ts`
- Create: `web/e2e/accessibility.spec.ts`
- Create: `web/e2e/visual.spec.ts`
- Create: `web/src/components/Button/Button.figma.tsx`
- Create: `web/src/components/TextField/TextField.figma.tsx`
- Create: `web/src/components/AppShell/AppShell.figma.tsx`
- Create: `web/src/components/AppShell/Sidebar.figma.tsx`
- Create: `web/src/components/AppShell/MobileBottomNav.figma.tsx`
- Modify: `docs/product-ui/figma-handoff.md`
- Modify: `web/package.json`
- Modify: `web/package-lock.json`

- [ ] **Step 1: Install Playwright and accessibility tooling**

Add Playwright test and axe packages. Configure `webServer` to start the production-equivalent unified server (or a documented test fixture server), not only the Vite UI. Set screenshot animations to disabled and honor reduced motion.

- [ ] **Step 2: Configure three named browser projects**

```ts
projects: [
  { name: "desktop", use: { viewport: { width: 1440, height: 1024 } } },
  { name: "tablet", use: { viewport: { width: 1024, height: 768 } } },
  { name: "mobile", use: { viewport: { width: 390, height: 844 } } },
]
```

Use one Chromium version from the Playwright lock. Do not mix browser versions in the initial baseline.

- [ ] **Step 3: Test the real user flow and responsive navigation**

`auth-shell.spec.ts` must register a unique test user, arrive at `/overview`, navigate through every shell destination, open the mobile More drawer where applicable, log out with CSRF and confirm a protected URL returns to `/login`. Tests use a disposable `PDF_ASSISTANT_DATA_DIR` and clean it after the server stops.

- [ ] **Step 4: Add axe and stable visual baselines**

Run axe against login and authenticated shell at all three viewports with zero serious/critical violations. Visual screenshots cover login, shell, mobile More drawer and validation/server error states. Mask only inherently dynamic values; do not mask layout areas.

- [ ] **Step 5: Pull exact Figma context before final CSS comparison**

Load `figma-design-to-code` before calling design-context tools. Read the exact component and screen node IDs recorded in `docs/product-ui/figma-handoff.md`. Compare implementation screenshots with Figma screenshots at matching viewport size, then fix code tokens/layout—not the approved Figma source—unless the source itself contains an obvious defect.

Expected: spacing, typography, colors, navigation behavior and breakpoints match the approved design; any deliberate browser rendering variance is documented in handoff.

- [ ] **Step 6: Add Code Connect mappings to real component nodes**

Load `figma-code-connect`. Generate mappings for Button, TextField, AppShell, Sidebar and MobileBottomNav using the actual Figma component URLs/node IDs. Props must map to existing code props and variants; no invented code API.

Example shape (replace the URL with the exact component URL):

```tsx
figma.connect(Button, actualComponentUrl, {
  props: {
    hierarchy: figma.enum("Hierarchy", {
      Primary: "primary",
      Secondary: "secondary",
      Ghost: "ghost",
      Danger: "danger",
    }),
    disabled: figma.boolean("Disabled"),
  },
  example: ({ hierarchy, disabled }) => (
    <Button hierarchy={hierarchy} disabled={disabled}>继续</Button>
  ),
});
```

Validate mappings through the Code Connect CLI configured for the repository. Update the handoff table with component name, code file, Figma node URL and validation status.

- [ ] **Step 7: Run unit, E2E, accessibility, visual and Code Connect checks**

```powershell
Set-Location web
npm test
npm run typecheck
npm run build
npm run e2e
# Run the repository-configured Code Connect validation command.
Set-Location ..
```

Expected: all three projects pass; screenshots are generated/updated only after deliberate review; Code Connect reports every mapping valid.

- [ ] **Step 8: Commit the verified design-to-code bridge**

```powershell
git add web/playwright.config.ts web/e2e web/src/components `
  web/package.json web/package-lock.json docs/product-ui/figma-handoff.md
git commit -m "test: verify Figma authentication shell across viewports"
```

---

### Task 8: 完成第一期全量回归、文档与发布门槛

**Files:**
- Modify: `README.md`
- Create: `docs/product-ui/README.md`
- Create: `tests/deploy/test_product_ui_readme.py`

- [ ] **Step 1: Add a failing documentation contract test**

Assert the root README contains `知研`, React development command, unified Uvicorn command, `/legacy`, `/healthz`, single-worker constraint and a link to `docs/product-ui/README.md`.

- [ ] **Step 2: Document supported development and production paths**

`docs/product-ui/README.md` must contain:

- product routes and the first-slice scope;
- exact Figma handoff link;
- design token ownership and Code Connect verification;
- local backend/frontend commands;
- production unified command and Docker build;
- Cookie/CSRF behavior and `APP_COOKIE_SECURE` requirement behind HTTPS;
- `/legacy` deprecation window and rollback procedure;
- three target viewports and screenshot-baseline update policy;
- explicit list of next slices: documents, QA, search, notes, insights.

- [ ] **Step 3: Run the complete Python suite in the repository venv**

```powershell
New-Item -ItemType Directory -Force .runtime\pytest-product-ui | Out-Null
$env:TEMP=(Resolve-Path '.runtime\pytest-product-ui').Path
$env:TMP=$env:TEMP
.\venv\Scripts\python.exe -m pytest -q --basetemp=.runtime\pytest-product-ui\base
```

Expected: all runnable tests pass. Record the exact new pass/skip totals; do not reuse the historical totals from `PROJECT_KNOWLEDGE.md`.

- [ ] **Step 4: Run complete frontend and design checks**

```powershell
Set-Location web
npm ci
npm test
npm run typecheck
npm run build
npm run e2e
Set-Location ..
```

Expected: clean install succeeds from lockfile; unit, type, build and all three Playwright projects pass.

- [ ] **Step 5: Run unified-server smoke checks**

Use a temporary data root, one Uvicorn worker and a non-default port. Verify `/healthz`, React history fallback, registration, session restore, CSRF rejection, CSRF logout and `/legacy`. Confirm the worker pool starts once and no test process remains afterward.

- [ ] **Step 6: Inspect repository and prevent unrelated staging**

```powershell
git diff --check
git status --short
git diff --stat
git diff --name-only --cached
```

Expected: no generated `web/dist`, Playwright HTML report, trace, runtime database, uploaded document, `.env`, token or current GraphRAG task-packet user edits are staged.

- [ ] **Step 7: Commit documentation closure**

```powershell
git add README.md docs/product-ui/README.md tests/deploy/test_product_ui_readme.py
git commit -m "docs: document product UI development workflow"
```

- [ ] **Step 8: Produce the phase completion report**

Report:

1. Figma file link and verified component/screen node links;
2. exact changed modules and architectural boundary preserved;
3. Python, frontend, Playwright, axe, visual and Code Connect results;
4. commit list and whether pushed;
5. known limitation: feature pages still route to migration empty states and `/legacy` until their later vertical slices;
6. recommended next implementation plan: documents/import/progress slice.

---

## Plan Self-Review

### Design/spec coverage

| Approved requirement | Plan coverage |
|---|---|
| Brand `知研` + subtitle | Tasks 1, 5 |
| A workspace / A2 emerald academic direction | Tasks 1, 5 |
| Desktop + tablet + mobile | Tasks 1, 5, 7 |
| Figma as token/component/page source | Tasks 1, 7 |
| React + TypeScript + Vite | Task 5 |
| FastAPI thin adapter | Task 4 |
| Shared application services | Task 2 |
| Cookie + CSRF auth | Tasks 3, 4, 5 |
| Gradio at `/legacy` for one release | Task 6 |
| Single process/single worker | Global constraints, Task 6 |
| Code Connect | Task 7 |
| Python/unit/E2E/axe/visual validation | Tasks 2–8 |
| No product-domain rewrite | Global constraints and all API boundaries |

### Priority rationale

1. **P0 — Shared lifecycle:** must precede FastAPI to prevent duplicate workers and split session state.
2. **P0 — Auth boundary:** required before any user-scoped React data is exposed.
3. **P0 — Design tokens/app shell:** establishes the cross-viewport contract before feature pages multiply.
4. **P0 — Unified runtime:** makes the new UI deployable while preserving `/legacy` rollback.
5. **P1 — Code Connect/visual verification:** closes Figma-to-code drift before the next vertical slice.
6. **P1 — Full regression/docs:** turns the slice into a repeatable product-development baseline.

### Consistency and safety checks

- Dynamic Figma identifiers are always copied from tool responses and validated before commit; none are guessed.
- React never reads the HttpOnly cookie and never persists CSRF/session data to browser storage.
- API response DTOs never expose `user_id`, tokens, runtime paths or assistant objects.
- Gradio remains behavior-compatible and shares the exact same services; no second worker pool is created.
- SPA fallback cannot swallow `/api` or `/legacy` errors.
- Page placeholders are explicit migration states with a working legacy action, not fake product data.
- Docker stays non-root and one-worker; production entrypoint has one authoritative process.
- Every task has a failing-test gate, focused verification and isolated commit.
- Existing unrelated dirty GraphRAG task-packet files are excluded from every `git add` command.

**Plan complete and saved to `docs/superpowers/plans/2026-08-05-figma-product-ui-foundation-auth.md`.**
