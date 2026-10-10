# 隔离认证导入运行时进度

## 2026-10-11：Task01 已验收

首个任务完成 PostgreSQL 普通领取、过期恢复、发布恢复领取的可选租户白名单。白名单在 SQL 候选选择前生效；任务锁和锁后重读绑定原候选用户。缺省参数保留旧行为，空白名单不访问数据库，非法参数在数据库访问前拒绝。

- 源码交付：`d81f0c3494d02bca51fb312c602d215edf6f98a0`，父提交 `322f8d45bfb42d54fa471e77d9e311534f92ce2e`。只包含两个仓库文件、一个验证 helper、两个测试文件及首包交付记录。
- 新增完整测试：28 passed，65.79 秒；相关三个文件完整回归：194 passed，944.33 秒。实际退出码均为 0，无失败、错误、跳过，共 222 个不同测试。独立无环境变量单测的 15 项已包含在新增测试中，不重复累计。
- Sol High 实现；Astra High 独立 Spec PASS / Quality PASS。最终报告 `.runtime/isolated-import-runtime/p1-final-accepted-astra-n4.md`，SHA256 `d911fcc66e1c920034cf8e57752fe9836e8049fb03dffe846b14286f81004cd5`。
- 根核验 `.runtime/isolated-import-runtime/p1-delivery-root-n3.json`；提交绑定及实际推送回执 `p1-commit-delivery-root-n3.json`。五个源码/测试文件的 Git blob 与实际测试字节一致。
- 相对此前已验收 74 文件，只有两个授权仓库文件变化，其余 72 个和 15 个预存工作区改动原始字节未变。
- Docker 恢复只启动指定 PostgreSQL、S3、目标 Qdrant，身份及卷不变。最终端口为 59497、59498、49742；受保护源 Qdrant 仍停止。
- 分支推送实际退出 0，输出确认 `322f8d4..d81f0c3` 已推送。推送后的两次远端读取因连接错误退出 128，因此尚无独立远端读取成功证据。

首轮回归 n1 在会话边界中断，缺退出码/XML，不计验收；旧 GREEN n1 的 26 项不覆盖后来新增两项并发测试。最终验收使用 expanded GREEN n3 和 regression n3 的完整证据。

下一步准备 Task02 隔离配置与就绪检查。当前没有隔离 API、独立 Worker 或完整业务迁移验收；历史向量回忆仍待来源证明。本进度不代表 main 合并或生产切换。
