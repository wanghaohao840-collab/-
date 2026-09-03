# 知研产品 UI 收尾修复设计

**日期：** 2026-08-09
**状态：** 已批准设计，待实施计划
**适用分支：** `codex/figma-product-ui-foundation`

## 目标

关闭当前产品 UI 分支剩余的五类非阻塞问题：验证 Docker Linux 容器交付、清除 npm advisory、消除 Starlette TestClient 弃用警告、移除无效的“保持登录状态”承诺，并完成两项 Penpot 设计源清理。修复不得削弱 Cookie/CSRF、会话隔离、单一 `ApplicationServices` 生命周期、RAG/Memory 边界或 `/legacy/` 回退路径。

## 已确认决策

### 登录会话

- 删除 React Login 页中的“保持登录状态”控件。
- 从 Penpot 的桌面、平板、手机 Login 设计中同步删除 remember 行。
- 保留现有浏览器 session cookie、进程内 SessionRegistry 与 12 小时滑动空闲过期语义。
- 不增加持久会话表，不向 localStorage/sessionStorage 写认证秘密，不修改 CSRF 轮换、Cookie Secure/SameSite/HttpOnly 策略。

### 依赖维护

- 将前端直接开发依赖 `ajv` 精确升级到 `8.20.0`；保留兼容的 `ajv-formats`。
- 目标是 `npm audit` 和 `npm audit --omit=dev` 均为 0。
- 为 Python 测试环境增加 `httpx2==2.9.1`，让 Starlette TestClient 使用受支持实现。
- 不在本轮升级 FastAPI、Starlette 或现有 `httpx<1`，也不把同步 TestClient 测试整体改写为 ASGITransport 异步测试。

### Penpot 清理

- 删除 `00 Foundations` 页面中 ID 为 `0f745b42-1a51-801c-8008-6ff39f5b8841` 的空白 `100×100` 画板；保留 `Board / Foundation System`。
- 在 TextField 与 PasswordField 主组件内部将 Input 子层改为横向 fill；PasswordField 的 spacer 同步 fill，44×44 eye 控件保持贴右。
- 不把组件或实例宽度硬编码为 400px。桌面实例应自然填满 400px 表单，平板和手机实例继续适配各自容器。
- 删除三档 Login 中的 remember 行后，按既有 token 和垂直节奏重排，不改品牌、文案、认证字段或按钮语义。
- 写入后复核所有 Login/Register 实例的组件链接、bounds、文字溢出和画板溢出；重新导出所有受影响的 Penpot 参考图。

### Docker 验收

- 使用当前已运行的 Docker Desktop Linux Engine 29.6.2 与 `desktop-linux` context。
- 为本轮创建唯一 Compose project 名、`127.0.0.1:17860` 端口和独立数据根；不得停止、重建或复用当前占用 7860 的容器。
- 从 `deploy/.env.example` 创建仅用于本地验收、受 `.gitignore` 排除的配置。浅层 smoke 不要求真实 LLM 凭据。
- 验证 app/Qdrant 镜像构建、容器健康、app 非 root 用户、Qdrant ready、`/healthz`、SPA history、`/legacy/`、卷读写及 `/app/hello_agents/__init__.py` 导入路径。
- 不执行会调用外部 LLM、产生凭据和计费要求的 deep smoke；它不是 Docker daemon、镜像或容器运行时的验收条件。
- 无论成功或失败，只按唯一 project 名和已经校验的隔离数据路径清理由本轮创建的容器、网络、卷和文件。

## 实施边界

本轮分为四个可独立审查的交付：

1. **依赖安全与测试兼容性**：升级 Ajv、加入 httpx2，清除 advisory 和 TestClient warning。
2. **真实登录界面**：删除 inert remember 控件，同步 React 测试与 Penpot 三档设计。
3. **Penpot 组件及 Foundations 清理**：修复 fill 约束、删除空板、重新导出受影响设计参考。
4. **Docker 及最终发布门禁**：隔离构建/smoke/清理，并执行完整回归和最终审查。

各任务必须先写会在旧实现上失败的测试或结构断言，再做最小实现。不得并行写同一工作树，不得把警告简单过滤或把安全 advisory 记录为例外。

## 数据流与安全不变量

- Login 仍只提交 `username` 和 `password`；删除控件不会改变 API payload。
- session cookie 仍为 HttpOnly、SameSite=Lax、Path=/，`Secure` 由 `APP_COOKIE_SECURE` 决定，无 Max-Age/Expires。
- CSRF token 仅经登录/session JSON 返回并保存在前端内存；修改请求继续携带 `X-CSRF-Token`。
- 服务端 session token 继续绑定服务端查得的 `user_id`；多用户目录、同用户多 session 和会话级当前文档隔离不变。
- Penpot 写入只允许触及已列明的空板、输入组件内部布局和 Login remember 行；若活动文件、页面或节点 ID 不匹配，立即停止写入。
- Docker 验收配置、容器资源和数据根不得进入 Git，也不得复用现有部署的数据目录。

## 错误处理

- `npm audit` 若仍报告 advisory，记录精确依赖链并停止该任务，不使用 `--force` 进行越级升级。
- TestClient warning 若在加入 httpx2 后仍存在，定位实际导入路径，不用 warning filter 掩盖。
- Penpot 任一节点身份、父级或组件实例关系含糊时，不写设计并报告阻塞；写后必须 fresh-read 原 ID。
- Docker 构建或 smoke 失败时保留有界日志摘要，执行 `compose down` 只作用于唯一 project；删除数据根前验证绝对路径位于本工作树预定目录。
- 完整回归发现与本轮无关的失败时，不扩大修改范围，先形成证据并请求方向。

## 验收标准

### 依赖

- 根 Ajv 为 `8.20.0`，`ajv-formats` 依赖解析正常。
- `npm audit` 与 `npm audit --omit=dev` 均为 0。
- 项目 venv `pip check` 为 0。
- 以 `StarletteDeprecationWarning` 作为 error 导入 TestClient并运行 API 测试，无该 warning。

### React 与认证

- Login DOM、表单提交和三视口视觉中均不存在 remember 控件。
- Login/Register 认证、session restore、logout、401 generation、Cookie 与 CSRF 测试保持通过。
- 前端 unit、typecheck、lint、build 与 Playwright 三视口通过。

### Penpot

- `00 Foundations` 顶层不再存在空白 100×100 画板，Foundation System 不变。
- Desktop Login 的 Username Input、Password Input 与 Submit 均从 `x=900` 延伸到右边界 `1300`；输入高 44、按钮高 48。
- Password eye 控件保持 44×44；全部 Login/Register 实例 linked，文字和实际 bounds overflow 为 0。
- 受影响参考 PNG 由真实 Penpot 导出，并与更新后的浏览器基线逐张人工检查；不遮罩布局差异。

### Docker

- 当前工作树的 app/Qdrant 镜像构建成功。
- 独立 Compose stack 中 app 和 Qdrant 均 running/healthy。
- app 容器以非 root UID 运行。
- 默认 smoke 验证 health、legacy config、Qdrant ready、卷读写和项目本地包导入，exit 0。
- 验收后本轮容器、网络和隔离数据根均已清理，原有 7860 stack 状态不变。

### 最终门禁

- 完整 Python suite 记录本轮 pass/skip/warning 总数。
- design/token/mapping、frontend、Playwright、unified smoke 全部通过。
- `git diff --check`、secret、Figma、raw-color、runtime/generated-artifact 扫描通过。
- 每个任务经过独立 reviewer；最终整分支审查无 Critical/Important。

## 非目标

- 不实现 30 天或可配置的持久登录。
- 不增加设备会话列表、全部设备退出或持久 session 数据库。
- 不升级 FastAPI/Starlette/httpx 主版本或重写全部 API 测试。
- 不运行需要真实 LLM 凭据和计费的 deep Docker smoke。
- 不修改 RAG、Memory、Qdrant 检索、文档导入或产品功能页范围。
