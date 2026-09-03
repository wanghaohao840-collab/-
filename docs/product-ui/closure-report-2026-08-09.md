# 知研产品 UI 闭环报告

本报告对应 2026-08-09 发起、2026-08-11 完成的产品 UI 闭环验收。范围包括依赖告警、
认证界面语义、Penpot 设计源、桌面/平板/手机视觉基线，以及 Docker Desktop Linux
发布链路。所有运行验证均基于 `codex/figma-product-ui-foundation` 当前工作树；未部署生产、
未推送镜像、未运行 deep smoke，也未使用真实 LLM 凭据。

## 已闭合事项

- 前端依赖使用 lockfile 固定的 Ajv 版本；`npm audit` 与 `npm audit --omit=dev` 均要求为零。
- 开发测试依赖固定 `httpx2`，完整 Python 门以 `StarletteDeprecationWarning` 作为错误处理。
- 登录页移除了没有持久化行为的“保持登录状态”；Cookie、CSRF、内存认证状态和 12 小时
  滑动过期语义保持不变。
- Penpot 的 Desktop、Tablet、Mobile Login 均移除该控件；TextField 与 PasswordField
  内部输入层按父表单宽度填充，PasswordField 可见性按钮保持 `44 × 44`。
- 浏览器保存 16 张受控视觉基线，覆盖三档 Login、AppShell、错误态、会话过期态和 More 抽屉。
- Docker 多阶段构建只编译应用 TypeScript 项目；Linux shell 脚本由 `.gitattributes`
  强制以 LF checkout。

## Penpot 设计门

- 文件：`知研 · 智能文档学习助手`
- 文件 ID：`3be9e5e1-190f-8090-8008-6ff3f3dcd54c`
- fresh-read：7 个唯一页面、映射组件/Variant 轴及 6 个交接画板均可按固定 ID 读取。
- Login 权威画板：Desktop `1440 × 1024`、Tablet `1024 × 768`、Mobile `390 × 844`。
- 三张 Login 参考图均由真实 Penpot 画板重新导出；表单无断链、文字溢出或画板越界。

## Docker Desktop Linux 隔离验收

环境为 Docker Engine `29.6.2`、Compose `5.3.1`、`desktop-linux` context。验收使用唯一
项目 `zhiyan-closure-20260809`、绑定 `127.0.0.1:17860`、工作树内临时数据根和无效占位
LLM 配置。命令顺序如下：

```powershell
docker compose --env-file .runtime/closure-docker/deploy.env -p zhiyan-closure-20260809 build app qdrant
docker compose --env-file .runtime/closure-docker/deploy.env -p zhiyan-closure-20260809 up -d app qdrant
& 'D:\python_self_agent\venv\Scripts\python.exe' deploy/smoke_test.py --env-file .runtime/closure-docker/deploy.env
docker compose --env-file .runtime/closure-docker/deploy.env -p zhiyan-closure-20260809 down --remove-orphans
```

运行证据：

- app 与 Qdrant 均为 healthy；镜像默认用户是 `10001:10001`，本次 Compose 按
  `APP_UID/APP_GID` 以 `1000:1000` 运行；入口以 `exec` 启动
  `python -m uvicorn server:app ... --workers 1`。
- `/healthz` 与 React history 路由 `/overview` 返回 `200`；`/legacy` 返回 `307` 并指向 `/legacy/`；
  `/legacy/config` 返回 `200`；Qdrant readiness 通过。
- `/app/data` 写入探针通过；仓库浅层 smoke 的 health、Gradio、Qdrant、数据写入和本地导入
  全部通过。
- app 镜像 ID 为 `sha256:666bd2272695f730eb3de44a628740354d2d66b0de840df0dc26b66779aedf41`，
  最终 `10001:10001` app 容器 ID 为 `75536a389bb526b40d04d831c96bb6cebb61c1de71cd9493bc572857514fc1c5`；
  Qdrant 镜像 ID 为 `sha256:31361506900a4d239a142a790c976e81fc442c4e28eecc4a02945215e3c1d05e`，
  最终容器 ID 为 `ff55a51933321e6aac96aba7acf11337f8e67f1796863aab43db1b20d4363b7f`。
  两者后续均按唯一项目名精确移除。
- 隔离容器和网络归零，临时数据根已删除。既有 `python_self_agent-app-1`
  (`0181c6abff3a...`) 与 `python_self_agent-qdrant-1` (`b57faada1f90...`) 的容器 ID、
  镜像、healthy 状态和 `0.0.0.0:7860` 端口在验收前后保持不变。

构建后的首次启动还真实发现并关闭了 Windows CRLF shebang 缺陷；入口脚本现在由仓库属性
保证 LF，镜像契约测试会拒绝 `CRLF` 回归。

## 完整回归

最终验收命令：

```powershell
& 'D:\python_self_agent\venv\Scripts\python.exe' -m pytest -q -W error::starlette.exceptions.StarletteDeprecationWarning --basetemp=.runtime/pytest-product-ui-closure
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

最终结果：

- Python：`803 passed, 7 skipped`，耗时 `711.30s`；`StarletteDeprecationWarning`
  作为错误门通过。输出中的 61 条 warning 均为既有 Neo4j driver 析构关闭提示；
  没有本轮要关闭的 Starlette/httpx TestClient 警告。
- Design：DTCG freshness 通过；组件映射、handoff 与真实 PNG 契约 `17/17` 通过。
- Dependency：`npm ci` 安装 274 个包；`npm audit` 与 `npm audit --omit=dev` 均为
  `found 0 vulnerabilities`。
- Frontend：Vitest `65/65`；typecheck、ESLint、完整 build 和 Docker 使用的 `build:app`
  均通过，Vite 转换 105 个 modules。
- Browser：Playwright 单 worker、禁止更新快照，`28 passed, 2 intentional skipped, 30 total`；
  覆盖 desktop/tablet/mobile 的认证、CSRF、axe、键盘焦点、`/legacy/` 和 16 张视觉基线。
- 文档与镜像契约：闭环文档/依赖契约 `6/6`；Docker 镜像契约 `9/9`。

## 残余风险与边界

- deep smoke 需要真实外部模型凭据，本次按安全边界明确不运行；这不是本地发布门的阻塞项。
- 本轮只验证并清理唯一隔离 Compose 项目，没有替换、重启或迁移现有 `7860` 部署。
- Penpot 插件连接凭据、Cookie、token 与本地 `.env` 均未写入仓库或报告。
- Python 完整套件仍会报告既有 Neo4j driver 析构弃用提示；它与本轮已消除的 TestClient
  警告无关，不改变通过结果，但后续 Neo4j 生命周期专项可将 driver 显式关闭作为独立改进。
