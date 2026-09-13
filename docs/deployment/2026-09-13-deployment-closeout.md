# 部署代码收尾与重启、恢复验证

## 本次变更

- `9fec2d1`：将维护启动入口、学习迁移协调器、Windows 冷备份/恢复适配器及运维脚本纳入 Git。修正命名卷备份的恢复目录检查；固定镜像部署拒绝隐式 embedding build/切换。
- `e45b4f4`：笔记首次查询完成前显示加载状态，避免误显示空笔记库；补充长标题、约万字正文和八条来源的跨尺寸布局验收。
- 新增 `deploy/Dockerfile.frontend` 和 `deploy/windows/Publish-Frontend.ps1`，将原先仅存在现场目录的前端分层发布方式固化入库。发布使用共享运维锁、配对冷备份、固定镜像、健康检查和失败回退。

## 已部署镜像与现场证据

- 当前前端：`zhiyan-app:notes-loading-e45b4f4`，镜像 ID `sha256:ae5da95c12d8a56280d9da2a60ca300515c89fcdcd2dba2f8c36fb7ef2d49938`。
- 直接回退镜像：`zhiyan-app:workspaces-7620809`，`sha256:71fc0b2677aefe18c83f4766aab2bf2e31f7d35c035ee2be16b50ad4c888ee86`。
- 更早的后端相同镜像：`zhiyan-app:rollback-before-workspaces-7620809`，`sha256:f5fd0cb311870617de3bb4d534e1c3d05589b37d66b84d2461a11f5914cdfd4d`。两份回退镜像均保留。
- Qdrant：`sha256:59f9f7fb8494896adbb0dc63fae731dfa0cdd564a40af45222b30ff6e5c3f21f`。
- 11:02 本机发布完成；入口 SHA256 `da46694d955975355f188a7259d2758eb40c0ebc39b5944693a2e450b8ec958b`。
- 发布前配对备份：`D:\python_self_agent_backups\daily\assistant-20260913T030147Z.tar.gz` 及同名前缀 `.qdrant-volume.tar.gz`。
- 现场结果：`deploy-state/ui-release-e45b4f4/{backup,result,verification}.json`，含原 Compose 快照 `rollback.yaml`。

本次生产变更仅增加前端资源层，已验证基础镜像的所有后端层保持一致。提交部署源代码不等于将所有部署工具作为新后端部署；没有对当前已迁移数据库再次执行历史学习迁移。

## 验证结果

- 部署全套测试：254 passed、1 skipped；新增维护测试后及运维修正后的相关回归：92 passed；新归档安全测试：6 passed。
- 前端单元测试：204 passed。首次高并发运行一个检索测试超过 5 秒；限制两个 worker 后全套通过，无需修改测试超时。
- 前端构建通过；保留原有大于 500 kB 包体积提示。
- 桌面、平板、手机现有视觉快照、可访问性和点击目标检查：3 passed。
- 长标题、长正文、无空格文档名、8 条来源布局：3 passed；无页面或来源卡片横向溢出。该测试使用明确的测试夹具，不产生生产业务数据。
- App/Qdrant 冷停后恢复健康；发布后所有 HTTP 静态资源与构建逐文件哈希一致，笔记、检索入口正确。未登录私有接口返回 401。
- 发布前后 SQLite 完整性正常，全部表记录数一致。

## 隔离恢复

使用 10:47 的配对备份 `assistant-20260913T024751Z.tar.gz`，在新目录、新命名卷和仅内部网络中恢复；没有发布端口、连接生产网络或挂载生产数据。

演练先启动 f5fd0cb 旧镜像，再启动 71fc0b2 工作台镜像：两个镜像均健康，笔记页面和 Qdrant 两个集合可访问，数据库表数值及文档文件哈希不变。临时容器、网络和卷均已删除。证据为 `.runtime/restore-drills/zhiyan-drill-b41cc2f65c7b406c/result.json`；数据副本仅留在本机忽略目录。

恢复必须提供与备份匹配的非敏感嵌入配置，包括 `QDRANT_COLLECTION`。缺少 provider 会被拒绝降级；缺少 collection 会触发 `entry_missing`。演练使用占位 API key 并禁止外网，不验证真实模型请求。

`deploy/verify_isolated_restore.py --help` 列出参数。`--embedding-profile` 指向 JSON，只允许以下六个字段，不接受密钥：

```json
{
  "RAG_EMBEDDING_PROVIDER": "siliconflow",
  "RAG_EMBEDDING_BASE_URL": "https://api.siliconflow.cn/v1",
  "RAG_EMBEDDING_MODEL": "BAAI/bge-m3",
  "RAG_EMBEDDING_DIMENSION": "1024",
  "RAG_EMBEDDING_REVISION": "siliconflow-bge-m3-v1",
  "QDRANT_COLLECTION": "doc_learning_vectors"
}
```

## 从 Git 重建与发布

完整源码构建使用根目录 Dockerfile；维护入口及其 COPY 依赖现在均已跟踪。实际生产采用保留既有后端镜像的前端分层方式：

1. 检出 `e45b4f4` 或后续文档提交。使用 `web/package-lock.json` 执行 `npm ci`、测试和 `npm run build`。
2. 创建新的独占发布目录，复制构建出的 `web/dist` 到其中的 `dist/`；将当前私有 `compose.release.yaml` 复制为 `rollback.yaml`。
3. 使用 `docker image inspect` 核实已有后端标签与上述固定 ID 一致，再用 `docker build -f deploy/Dockerfile.frontend --build-arg BACKEND_IMAGE=<核验过的本地标签> --build-arg WEB_REVISION=<源码提交> -t <新标签> <发布目录>` 构建。
4. 检查候选镜像 ID，运行 `Publish-Frontend.ps1`，显式传入 `RepositoryRoot`、`ReleaseDirectory`、`BaselineImage`、`CandidateImage`、`Revision`、`BackupRoot`。该脚本面向本机 `desktop-linux`、`python_self_agent` Compose 项目；其他项目需要先调整并验证名称。
5. 保留配对备份、`rollback.yaml`、结果清单和旧镜像。只回退 UI 时在运维锁内恢复 `rollback.yaml`，固定镜像 `up --no-deps --no-build --pull never app` 后复验健康；不要覆盖当前业务数据。数据库结构变化必须使用独立的迁移/恢复流程。

Docker 必须在 PATH 中；实际密钥和部署 env 通过本机已有私有配置提供，不进入 Git。Git 记录源码、依赖清单和构建方法；旧镜像和业务备份仍需独立保管。没有执行全新依赖下载的完整后端构建，不承诺浮动镜像和包源下的字节一致重建。

## 待补验

本轮重启后新建验收浏览器显示登录页，已请用户自行登录现有账号。此前真实账号验收见 `2026-09-12-workspaces-release.md`；本轮尚不能将重启后的真实账号会话和页面数据列为通过。
