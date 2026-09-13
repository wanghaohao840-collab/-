# Progress

## 2026-09-13 — 开始审查

- **Status:** active
- **Changes:** 记录用户授权范围；定位部署实现和历史 runbook，确认笔记列表未接收初次加载状态。
- **Verification:** 启动 `.\venv\Scripts\python.exe -m pytest tests/deploy -q --basetemp=.runtime/pytest-deploy-closeout`，结果待收集。
- **Next:** 审查维护适配器和测试覆盖，整理部署文件提交清单。

## 2026-09-13 — 发布与恢复通过，等待登录

- **Status:** waiting for real-account browser login
- **Changes:** 部署代码 9fec2d1、笔记加载修复 e45b4f4、验收记录 276d4be 已提交并推送。当前生产前端镜像 ae5da95，旧镜像和数据备份保留。
- **Verification:** 部署全套 254 passed / 1 skipped，相关回归 92 passed，归档安全 6 passed；前端 204 单测通过，3 个现有布局快照和 3 个长内容布局通过。配对备份在隔离环境依次启动旧、新镜像成功；数据库表数量和文档哈希不变。生产全部前端资源哈希、401 鉴权、数据库数量核对通过。
- **Evidence:** `docs/deployment/2026-09-13-deployment-closeout.md`；现场 `deploy-state/ui-release-e45b4f4/`；隔离 `.runtime/restore-drills/zhiyan-drill-b41cc2f65c7b406c/result.json`。
- **Next:** 用户已收到登录请求。本轮新建 in-app tab 1 停在本机登录页；用户登录后只读核对现有验收笔记、概念、标签、来源和文档列表，更新待补验记录。不要重复发布或再次运行旧迁移。
