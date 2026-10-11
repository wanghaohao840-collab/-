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

## 2026-10-11：Task02 实现与复审中

Sol High 在两个新文件实现显式配置和独立就绪探针。首轮无服务单测实际退出 0，17 passed；这是原始实现的局部证据，不能覆盖后续修订。未运行实施前的缺模块 RED，已记录这一执行顺序偏差，不制造替代证据。

Astra High 首轮独立审查为 Spec / Quality CHANGES REQUIRED，要求修复等价数据库 DSN 身份绕过、额外迁移头、并发客户端所有权、非法直接构造错误类型，并补齐必需覆盖。报告 `.runtime/isolated-import-runtime/p2-implementation-astra-n1.md` 绑定原始源和测试；修订后须重新冻结、完整测试和独立复审，当前未验收或提交 Task02 源码。

真实测试 n1 只有 WMI 启动收据，Docker 引擎管道不可用导致测试前退出，没有 pytest 结果，不计验收。根代理随后恢复 Docker Desktop，并仅启动指定 PG/S3/目标 Qdrant；四个容器身份与卷核验不变，受保护源 Qdrant 保持停止。PG ready 与 S3/目标 Qdrant HTTP 200 已确认；目标 Qdrant 的随机端口为 63103，PG/S3 仍为 59497/59498。修订后的运行器将保存测试前错误及实际退出码。

修订 n2 的无服务单测实际退出 0，30 passed，无跳过；真实服务测试实际退出 1，11 passed、2 failed，无错误或跳过。两个失败均在非空 History 夹具的 witness 写入前事务尚未执行 SQL，未满足 caller-owned active transaction 要求。完整新文件与回归尚未运行。源和测试在上述运行期间前后 SHA 一致，收据分别为 `p2-unit-n2-completion.json` 和 `p2-real-n2-completion.json`，不能将重叠轮次累加为交付测试数。

Astra n2 复审仍为 CHANGES REQUIRED：查询参数覆盖和编码主机名可绕过普通 PostgreSQL 身份拒绝，直接构造非字符串 DSN 仍逃逸合同错误类型。报告 `.runtime/isolated-import-runtime/p2-implementation-astra-n2.md`，SHA256 `f1548973810fb2f27bb3e87331a6338a2fa03ab1d25ad476e12a4ce447fa9e02`。额外迁移头和客户端生命周期竞争已静态核销；新增真实 ABA、头变更、有界重试和固定对象版本测试仍需最终通过证据。下一轮须修正这些问题及夹具后，重新冻结、重跑和复审。

## 2026-10-11：Task02 已验收并提交

Sol High 完成显式隔离配置、独立 PG 资源所有权、恢复与普通就绪探针。配置按 libpq 有效身份拒绝普通资源重用，迁移 ledger 必须恰好一项；生命周期锁避免并发创建/关闭泄漏。单条 SQL 捕获完整数据库权威，外部读取后完整重验最多一次，最终数据校验拒绝 Memory 行丢失及 invalid-valid-invalid ABA。

最终 n4 无服务单测 58 passed，真实服务测试 13 passed，完整新文件 71 passed（76.17 秒），相关回归 51 passed（180.49 秒）；实际 pytest/runner 退出均为 0，无失败、错误或跳过。共 122 个不同测试，不叠加重复选集。源 SHA256 `5ed6e0614596689969c97ac64f6754e9228b8a7128831da633ad550854fdb0a2`，测试 SHA256 `b16832c62a591707534654692200a67a366d5dfe48d6f15038f0b518b0f87513`。

Astra 最终独立 Spec PASS / Quality PASS：`p2-final-accepted-astra-n4.md`，SHA256 `1ee8dfa7a3bc225b88b42c6b15bb5f1eab73e0c1d14fe7e11f7d271b3c63cd26`。根 checker 实际退出 0，`p2-delivery-root-n4.json` 绑定日志/XML/字节及唯一测试数，并确认前序验收源码和 15 个预存改动未变。WMI 原始启动 JSON 的 literal newline 尾缀已通过独立派生绑定记录保留原件；完整 runner/pytest 完成收据有效。

n3 真实测试在 Docker 故障期间未进入 pytest。恢复中仅保留并重命名 Docker 的失效套接字目录，未重置配置或卷；原目录为 `run.p2-recovery-20261011-preserved`、`run.p2-recovery-n2-20261011-preserved` 和 `docker-secrets-engine.p2-recovery-20261011-preserved`。恢复收据 `p2-docker-recovery-root-n2.json`。最终 PG/S3/目标 Qdrant 端口为 59497/59498/49889；源 Qdrant 仍停止。此前失败记录继续保留。

Task02 接受范围仅为只读就绪组合，synthetic provider hook 不能证明真实嵌入服务或原子 admission。下一步为 Task03 共享认证与导入 facade 的当前代码核对、任务包和独立审查；真实 provider、独立 Worker、进程重启、完整迁移及生产切换均未验收。

源码提交为 `18e1f1b591e767373c9f81e4fd45c5c2f5b882f8`，父提交 `35d34888715b9a90bc5abfb8022353cd8f095ae3`，严格包含两个新源码/测试和三个 scoped 交付文档。`p2-commit-binding-root-n4.json` 确认源码 Git blob 与实际测试 raw SHA 完全一致。默认代理推送失败；仅对子进程去除失效代理后重试实际退出 0，输出确认 `d81f0c3..18e1f1b` 推送到授权分支。回执 `p2-push-direct-root-n5.json`。推送后独立远端读取退出 128（连接重置），`p2-remote-check-root-n5.json` 明确 `remote_read_verified=false`；不宣称独立远端 HEAD 核验成功。本进度更新的后续文档提交不改变验收源码。
