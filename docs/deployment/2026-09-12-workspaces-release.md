# 学习笔记与文献检索工作台部署

2026-09-12，经用户明确授权部署并修复 GitHub 代理。

## 镜像与代码

- 学习笔记：e83e293。
- 文献检索：7620809，包含此前设计与实现提交。
- 新镜像：`zhiyan-app:workspaces-7620809`，固定 ID `sha256:71fc0b2677aefe18c83f4766aab2bf2e31f7d35c035ee2be16b50ad4c888ee86`。
- 基础生产镜像：`sha256:f5fd0cb311870617de3bb4d534e1c3d05589b37d66b84d2461a11f5914cdfd4d`。
- 回滚标签：`zhiyan-app:rollback-before-workspaces-7620809`。

镜像从原生产镜像增加一个前端静态资源层。已校验新镜像除最后一层外与原镜像层完全一致；后端、依赖、启动修复保持原生产版本。本次不运行数据库迁移，不将工作区中其他未提交后端改动加入镜像。

`compose.release.yaml` 已固定新镜像，使用既有 127.0.0.1:7860 绑定、数据目录与 Qdrant 命名卷。发布持有既有 operations.lock，冷备份后更新 App，保留失败时切回原镜像的处理。

## 冷备份

- 应用：`D:\python_self_agent_backups\daily\assistant-20260912T091709Z.tar.gz`
- Qdrant：`D:\python_self_agent_backups\daily\assistant-20260912T091709Z.tar.gz.qdrant-volume.tar.gz`
- 两份归档 SHA256 均与各自 sidecar 校验一致。

现场清单、原/新 Compose 配置、部署与校验脚本保存在忽略目录 `deploy-state/ui-release-7620809/`。业务数据及这些现场文件未提交 Git。

## 验收

- App、Qdrant 健康；容器运行镜像 ID 与候选一致。
- HTTP 实际返回的 index、JS、CSS、图片与已验证的构建逐文件 SHA256 一致；/notes 与 /search 返回新版入口。
- 未登录访问笔记和文档私有接口均返回 401。
- 冷备份 SQLite 与当前 SQLite 均通过 quick_check，全部业务表记录数一致。
- 前端功能此前已通过 203 项单元测试及隔离测试服务中的桌面/平板/手机链路验收。本次生产验收未重新输入真实账号密码，不声称完成了新的真实账号登录验收。
- 原有大于 500 kB 主 JS 包的构建提示仍存在。

## GitHub 代理

仓库级 `http.https://github.com.proxy` 原为不可用的 `http://127.0.0.1:9567`。确认 Clash Verge 进程实际监听 7897 且可连接 GitHub 后，将本仓库配置更新为 `http://127.0.0.1:7897`。未改变系统或全局 Git 代理。`git push origin HEAD` 成功，远端分支 `codex/batch-import-async-tasks` 已包含两个功能提交。

## 2026-09-13 真实账号浏览器补验

用户在本机浏览器自行登录后，通过当前真实账号会话完成：选择既有验收文档 → 查询学习闭环 → 返回 1 条真实证据 → 查看来源摘录 → 复制引用（页面确认成功） → 编辑并保存来源笔记 → 修改概念和标签并保存 → 刷新页面 → 核对正文、概念、标签、已保存状态与 1 条来源 → Markdown 预览 → 来源面板核对原始摘录及关联概念。

新增笔记标题为「工作台部署验收 · 2026-09-13」，保留在用户账号中供复查。未修改旧笔记或删除资料，未读取或保存用户密码。本次复用已有验收文档，没有重新上传文件或生成新的 LLM 问答；这些链路不列为本次补验结果。
