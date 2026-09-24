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

## 2026-09-14 — 真实账号复验完成

- **Status:** complete
- **Verification:** Docker 应用与 Qdrant healthy；真实账号 `1127230940` 进入学习笔记，显示 2 条笔记。验收笔记正文、概念「学习闭环」、标签「部署验收 / RAG」、已保存状态、最近保存时间和真实文档摘录均正确；来源面板显示 1 条文献证据及关联概念「学习闭环」。
- **Result:** 部署收尾目标全部完成。未修改或删除用户资料，未读取密码。不要重复发布或历史迁移。

## 2026-09-24 — 当前镜像业务闭环与隔离故障回退

- **Status:** 当前单实例发布验收完成，可作为分布式升级的业务基线；尚未验收多实例运行。
- **Business verification:** 2026-09-14 在现有账号中导入验收文档，完成真实问答、原文引用、来源笔记保存与刷新，以及学习计划创建、任务完成和撤销的刷新持久化检查。现场证据保存在本机 `.runtime/ui-live-acceptance/acceptance-20260914-result.json`，不入库。
- **Rollback verification:** 2026-09-20 使用配对归档在独立目录、内部网络和新 Qdrant 卷上启动候选镜像，写入 SQLite 与 Qdrant 测试标记，强制停止隔离容器，再把配对备份恢复到全新目录和卷。前一镜像健康启动；数据库、文档文件及两个 Qdrant 集合的点数与内容哈希恢复到基线。演练资源清理完成，生产容器身份未变。现场证据保存在本机 `.runtime/restore-drills/zhiyan-fault-46571c278094/result.json`，不入库。
- **Source review:** 现有会话、助手实例及 CSRF 保存在 `app/session.py` 的进程内注册表；`app/coordination.py` 的用户写锁只作用于同一进程，`app/history.py` 的 JSON 更新依赖该锁。`app/summary_tasks.py` 的旧摘要任务也在进程内。相对地，产品问答与摘要走 `app/qa_job_repository.py` 的数据库租约，笔记投影走 `app/note_projection.py` 的租约，学习任务使用 `app/learning_repository.py` 的用户作用域、请求幂等和版本检查。当前代码没有发现妨碍以这些业务契约为基线逐步升级的问题，但不能直接增加 API 副本数。
- **Next design gate:** 确认先做单机多实例兼容层，还是直接实施已批准的跨主机 PostgreSQL/S3 方案。无论选择哪条路径，都保持单实例生产服务与配对回退路径，先在隔离环境验收。
