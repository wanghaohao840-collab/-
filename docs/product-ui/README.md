# 知研产品 UI 工作流

本目录记录知研 React 产品界面的受支持设计、开发、验证与发布流程。第一阶段
只交付认证流程和响应式应用外壳；Memory、RAG、文档隔离、引用、报告、批量
导入与存储仍由现有 `ApplicationServices` 边界提供，不在 React 中复制业务
状态或后端逻辑。

## 当前范围与路由

| 路径 | 当前职责 |
|---|---|
| `/login`、`/register` | React 登录与注册；成功后恢复目标页或进入 `/overview` |
| `/overview` | 受保护的默认落点；当前显示明确的迁移状态 |
| `/documents` | 文档库导航位；当前显示明确的迁移状态 |
| `/qa` | 智能问答导航位；当前显示明确的迁移状态 |
| `/search` | 文献检索导航位；当前显示明确的迁移状态 |
| `/notes` | 学习笔记导航位；当前显示明确的迁移状态 |
| `/insights` | 学习洞察导航位；当前显示明确的迁移状态 |
| `/api/v1/auth/*` | FastAPI 注册、登录、会话恢复与退出 API |
| `/legacy/` | 共享同一 `ApplicationServices` 的完整 Gradio 界面与回滚入口 |
| `/healthz` | 统一服务健康检查，返回 `{"status":"ok"}` |

迁移页不展示虚构产品数据，而是说明功能仍在迁移并提供 `/legacy/` 操作。下一批
垂直切片依次为 documents、QA、search、notes、insights；每个切片应复用现有服务
边界，并在端到端能力完成后替换对应迁移状态。

## Penpot 连接与交接

Penpot 是本产品 UI 的唯一视觉设计源。使用已登录的浏览器会话打开规范化文件
链接，不把登录 Cookie、`userToken`、API Key 或其他凭据复制到命令、日志、
截图、提交或文档中。仓库只保存 team/file 标识组成的无密钥链接。发布核验只读
取回文件、页面、组件和画板；除非另有明确设计任务，不在发布门禁中写入 Penpot。

文件名、无密钥 URL、七个页面 ID、组件 ID、六个参考画板 ID 与导出路径见
[`penpot-handoff.md`](penpot-handoff.md)。React 对应关系保存在
[`penpot-component-map.json`](penpot-component-map.json)，不得用名称猜测或替换
已经 fresh-read 核验的 ID。

## DTCG Token 与组件映射

[`design/tokens/zhiyan.tokens.json`](../../design/tokens/zhiyan.tokens.json) 是仓库内
受版本控制的 DTCG Token 契约，
[`web/src/styles/tokens.css`](../../web/src/styles/tokens.css) 是生成物，不应手工
维护。视觉值在 Penpot 批准后，同一变更中更新 DTCG 快照并重新生成 CSS：

```powershell
node scripts/design_tokens.mjs design/tokens/zhiyan.tokens.json web/src/styles/tokens.css
node scripts/design_tokens.mjs --check design/tokens/zhiyan.tokens.json web/src/styles/tokens.css
node --test tests/design/test_design_tokens.mjs tests/design/test_penpot_component_map.mjs
```

最后一条命令同时验证 DTCG、映射 schema、无密钥 URL、组件 ID 唯一性、代码文件
存在性和 `verified` fresh-read 标记。映射只覆盖公开 React 组件契约，不改变
Penpot 文件本身。

## 本地开发

使用项目正式 venv，不使用系统 Python 或 Anaconda。先在仓库根目录启动后端：

```powershell
.\venv\Scripts\python.exe -m uvicorn server:app --host 127.0.0.1 --port 7860 --workers 1
```

再在另一个 PowerShell 中启动前端：

```powershell
Set-Location web
npm ci
npm run dev
```

Vite 将 `/api` 和 `/legacy` 代理到 `http://127.0.0.1:7860`。首次检出或 lockfile
变更后运行 `npm ci`；普通前端迭代可直接运行 `npm run dev`。

## 统一生产进程与 Docker

生产构建先生成 `web/dist`，然后由一个 Uvicorn worker 同时提供 API、React SPA
和挂载在 `/legacy` 的 Gradio：

```powershell
Set-Location web
npm ci
npm run build
Set-Location ..
.\venv\Scripts\python.exe -m uvicorn server:app --host 0.0.0.0 --port 7860 --workers 1
```

Session、用户写锁和导入 worker pool 都是进程内资源；多 Uvicorn worker 或同时
启动第二个 Gradio 进程会分裂状态，因此不受支持。容器镜像使用多阶段构建并以
非 root 用户运行：

```sh
docker build -t zhiyan:local .
docker compose --env-file deploy/.env up -d --build
```

Compose 的秘密与运行数据约束、Qdrant/Neo4j 配置、备份和恢复流程见
[`deploy/README.md`](../../deploy/README.md)。不得提交 `deploy/.env`、运行数据库、
上传文档或报告。

## Cookie 与 CSRF

- 服务端把 session token 放在 `zhiyan_session` Cookie 中，属性为 `HttpOnly`、
  `SameSite=Lax`、`Path=/`；React 不读取 Cookie，也不把 session 或 CSRF 数据
  写入 `localStorage`/`sessionStorage`。
- 浏览器请求使用 `credentials: "same-origin"`。登录、注册与会话恢复响应只把
  username 和 CSRF token 交给内存中的认证状态，不返回 session token、用户 UUID
  或数据路径。
- 已认证的 `POST`、`PUT`、`PATCH`、`DELETE` 请求必须由客户端添加
  `X-CSRF-Token`；缺失或错误 token 返回 `403 invalid_csrf_token`。退出登录也遵循
  此规则，成功后服务端失效会话并清除 Cookie。
- 本地明文 HTTP 保持 `APP_COOKIE_SECURE=false` 或不设置。经过 HTTPS 网关的生产
  部署必须设置 `APP_COOKIE_SECURE=true`，使 Cookie 带 `Secure`；反向代理还必须
  保持同源路由语义。

## `/legacy` 回滚窗口

第一阶段发布期间保留 `/legacy/`，供尚未迁移的业务能力和 React 外壳故障时
回滚。回滚是把用户导向同一统一服务的 `/legacy/`，不是启动第二个进程；该挂载
共享同一 `ApplicationServices`、会话注册表和 worker pool。若 React 路由出现
问题，先确认 `/healthz` 正常，再直接使用 `/legacy/`，保留现场并回退相关前端
提交或镜像。不要为回滚提高 worker 数或复制数据目录。

## 视口、可访问性与截图基线

Playwright 固定三个目标视口：desktop `1440 × 1024`、tablet `1024 × 768`、
mobile `390 × 844`。认证、导航、键盘焦点与响应式行为在这些项目中验证；axe
要求 WCAG 2 A/AA 与 2.1 A/AA 下没有 serious/critical violation。

视觉基线位于 `web/e2e/visual.spec.ts-snapshots/`。截图禁用动画、隐藏插入光标并
启用 reduced motion。只有 Penpot 批准的视觉变化或经过审阅的有意实现变更才可
运行 `npx playwright test --update-snapshots`；更新前后必须以匹配视口对照
[`reference/penpot/`](reference/penpot/)，逐张检查布局、换行、状态和裁切。不要
用整块遮罩掩盖差异，也不要因为测试失败批量接受新基线；仅可遮罩不可避免的
动态值。失败产生的 `web/test-results`、报告、trace 和临时数据不得提交。

完整前端发布检查为：

```powershell
Set-Location web
npm ci
npm test
npm run typecheck
npm run lint
npm run build
npx playwright test
Set-Location ..
```
