# 可提交整合范围

本次在 `codex/batch-import-async-tasks` / `daeb24f` 上整理现有检索、来源笔记、
BGE-M3 运行时，并接入匹配当前 React 产品的 SQLite 学习中心。
交付方式为本地 Git 提交，代码与本记录一并保存；不推送、不部署。

## 文件与责任边界

`change-groups.json` 是本次提交文件的明确清单，按以下责任分组。共享装配文件保留在产品组，
不要将这些分组当作可以任意顺序单独 cherry-pick 的独立提交。

- `runtime`：嵌入身份、注册表、JSON/Qdrant 索引、来源读取、候选重建/验证、检索质量评估及契约测试。
- `product`：搜索、来源笔记、学习洞察、SQLite 学习计划/任务，共享服务/API/React 路由及对应测试和视觉基线。
- `deployment`：镜像构建依赖、配置示例和 Python 嵌入迁移入口。
  `deploy/windows/Update-Deployment.ps1` **只暂存**镜像构建的 `--pull --no-cache` 一处改动。
- `records`：此前检索与嵌入审查记录、产品上下文、本次整合证据和忽略规则。

提交后用 `git show --stat HEAD` 和 `git show HEAD` 审核本次代码。其他 Windows 运维/学习发布恢复脚本、
GraphRAG 历史记录变更仍保留在工作树，未纳入本次暂存。
不要使用 `git add -A` 覆盖这个边界；尤其不要整文件重新暂存上述更新脚本。

## 学习中心来源与兼容性

`learning-import.json` 记录从 `.worktrees/bge-m3-runtime-identity` 引入的路径。
依据是该工作树 `2026-09-06-learning-plans-today` 任务包中的实现和验收，
其中 07 包记录了三端计划、今日任务、完成/撤销、重启和删除生命周期。
旧 `codex/learning-mvp` 的 JSON 运行时没有引入当前产品。

- 当前树与来源树的搜索、笔记、受管理 RAG 实现一致；未覆盖为旧版接口。
- `ApplicationServices` 统一构建学习仓库/服务并接入已有文档库；新增表保留旧笔记/QA 表。
- 文档删除事务同时删除学习计划/任务、清理 QA 与直接文档来源；笔记正文保留。
  新增组合断言位于 `tests/test_note_source_deletion.py`。
- 学习请求继续按当前会话和文档删除 fence 校验权限。旧 `learning.json` 只触发显式迁移门禁，
  不自动恢复或导入。迁移底层代码的离线测试不等于生产迁移完成。
- 来源笔记继续重新解析片段身份与校验和；JSON/Qdrant 检索沿用索引身份隔离。
- 概览/洞察保持当前保留记录的统计口径，不把计划分钟数算作实际学习时长。

本次不执行部署、生产数据库升级或向量切换，不宣称学习卡片/练习生成已在 React 产品完成。

## 本地文件保护

暂存不包含 `.env`、数据库、上传文件、向量数据、备份或测试输出。新增忽略规则覆盖
现有 `deploy-data-backups/`、`deploy-data.rollback-*/`、`output/` 和临时目录；原文件未删除。
常见私钥/API token 模式检查无命中；配置示例仍可提交。日志位于忽略的 `.runtime/`。

验证结果、命令和已知限制见 `progress.md`。
