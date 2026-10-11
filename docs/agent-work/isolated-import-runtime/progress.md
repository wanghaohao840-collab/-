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

## 2026-10-11：Task03 任务包已通过独立复审

Astra High 首轮发现登录清理过期会话与导入用户锁之间的实际 FK 锁环，以及读回删除栅栏、对象引用 ABA、准入凭证失败回收和错误分类缺口。修订采用同事务 NOWAIT 会话重验、最终栅栏/引用/字节摘要证明、原对象身份的一次消费与 finally 回收，以及回滚释放锁后精确用户 gate 分类。六文件边界和 Task05 SharedSession/token 消费契约均已同步。

独立 n2 报告 `p3-plan-astra-n2.md` 判定 READY，无剩余阻塞项，SHA256 `a67ed9ab4525eb59020cfce41ecc11039096ca5f49ade36c5184baca9a9c8994`。根实现前基线 `p3-baseline-root-n1.json` 核验 78 项源码、15 个预存改动原始字节以及三个新文件尚不存在。任务包已设 ready，分配 Sol High；下一步先生成新测试并记录真实缺模块 RED，再实现和验证。此处只有任务包验收，没有 Task03 源码或测试通过证据。

任务包文档提交 `5646d155176902131db212d2310978cf9e46feb1` 已推送，实际退出 0，输出 `44e97cf..5646d15`；回执 `p3-ready-push-root-n1.json`，尚无独立远端读取核验。

随后独立 `ls-remote` 实际退出 0，远端隔离分支精确匹配 `5646d155176902131db212d2310978cf9e46feb1`；回执 `p3-remote-check-root-n1.json`，`remote_read_verified=true`。这仅验证该文档提交与祖先在隔离分支的边界，不代表 main 合并或 Task03 未提交源码已交付。

实施前 RED n1 已由 Sol 执行，两个新 facade 模块尚不存在。pytest 和 wrapper 实际退出 1，2 tests / 2 failures / 0 errors / 0 skipped；失败分别为两个缺模块导入。XML SHA256 `d8fbb6c8a5920588a69be859422c73d6972b24d8bc86c39ba0ccb59a2ea590f4`，日志 `a887bd778c06c7760e74bc969f40842906efeaef79cae019f5afcff313415e51`。根 checker 实际退出 0，`p3-red-root-n1.json` 绑定退出码、日志/XML/runner 和 81 项测试前后相同字节。没有 live fixture 调用。这是实际 RED 历史，不是 GREEN 通过证据；随后已授权 Sol 实现，仍须冻结后完整测试与独立实现审查。

Task03 初始实现冻结 n1 后，无服务测试实际退出 0，11 passed、19 deselected，无失败/错误/跳过，根 `p3-unit-n1-root-check.json` 核验日志/XML/字节及 15 个预存改动。该轮只有局部证据，不覆盖随后修改的测试或源码。

Astra 静态实现审查 `p3-implementation-astra-n1.md` 为 CHANGES REQUIRED，SHA256 `ee092a7bd1796563d6261b202cb94bf88c79c868394afb3f218393b06f4d9d34`：S1 要求 cursor 权威比较完成后重新以生命周期锁验证未关闭，防止已借出连接在 close 后完成准入；S2 要求身份漂移和非上传验证 ValueError 返回 503，收窄 400 上传错误边界。根另要求补强实际 manifest 不匹配、栅栏及引用 ABA 测试。已授权同一六文件范围内修正；尚未执行 Task03 真实服务测试、完整新文件或相关回归，未提交源码或验收 Task03。

S1/S2 修正后冻结 n3，无服务 unit n2 实际退出 0，17 passed、24 deselected，无失败/错误/跳过；根 `p3-unit-n2-root-check.json` 核验原始收据。real n1 因 PG 已停止在 preflight 退出 1，没有 pytest/XML；模型额度中断后的进程核对无残留 pytest。后续核验四个容器 ID/name/volume 未变，仅恢复指定 PG/S3/目标 Qdrant，源 Qdrant 仍停止。real n2 before 收据记录 59497/59498/52565 及通过的服务前检，当前结果待实际完成。

Astra 复审 `p3-implementation-astra-n2.md`（SHA256 `363f5eb55e0a3dfe8bcc0d837d812d709371f8e765ce39375ee01d51a854d811`）确认 S1/S2 源码静态通过，仍要求修正一项测试：真实 close 钩子在 prepare 阶段提前触发，未确保实际 stamp 已签发消费并进入最终事务回调。保持本轮测试字节冻结；结束后只修测试再复验。完整新文件和相关回归尚未运行；Task03 源码未提交、未验收。

real n2 最终实际 pytest/runner 退出 1，24 tests 中 23 passed、1 failed，无 errors/skips，322.84 秒；before/after 六文件字节不变。XML SHA256 `4e548532713d20d22677768673d50bb9fb10139729ba39a48a0aaf623be9a155`，日志 `7418c5ae371664c908eaa4480e1d2bfad159f12026bbc7c7888f4f48a436db85`。唯一失败为 equal-invalid-fence 测试插入漏填 NOT NULL created_at，属于测试夹具 SQL；已授权只修新测试文件的时间字段、close 回调覆盖和拒绝前无 I/O 显式计数，再冻结复验。23 passed 不作为最终整包通过证据。

最终测试修订冻结 n5（`p3-sol-freeze-n5.json`，SHA256 `6d22adba177b6c8639c8261789eea4c1027f0278a069b37b33dcd4c65a623f38`）；五源码不变，新测试 SHA256 `cb46a7899a8cf165d014ae727e96a9ad1e04618638f0e7b4391e6971a37cd205`。Astra 聚焦复核 n3 为 STATIC PASS（报告 `p3-implementation-astra-n3.md`，SHA256 `7f0a9be4e937af5911ef1ade3d7b85f53bcb0876712f37860d1a4b21770aece2`），T1/夹具/无 I/O 计数问题已闭合，仍不替代最终实际测试及验收。

同 n5 字节的 unit n3 实际退出 0，17 passed；real n3 实际退出 0，24 passed，237.86 秒，无 failure/error/skip。根 `p3-unit-n3-root-check.json` / `p3-real-n3-root-check.json` 均实际退出 0。真实 XML SHA256 `7e3b799bd6bd1e471f6e29279a55a31b193632ff2e9835d989cb98b8150ae382`，日志 `dc8c2380c1d48aaf05e043c568e8b435c74dcbe28cfaf64e0a3503d662899e74`。完整新文件与相关回归待完成，源码仍未提交。

full n1 完整新文件实际退出 0，41 passed，236.09 秒；regression n1 实际退出 0，112 passed，440.06 秒，均无失败/错误/跳过。根 stage checker 和合并 checker 均退出 0，`p3-delivery-root-n1.json` SHA256 `c6e0a490e706b594373b304f685ac48d8e390c76afca64d1e77e4ab433617c3b`：unit+real 精确等于 full，full 与 regression 不重叠，共 153 个不同测试；各阶段源/测试/runner 字节相同，15 个预存改动未变。完整 XML SHA256 `0d679cc68aa9cc4c886e5cfdc575eecc41a317645e9482d7b728fbbe04f8f89e`，回归 XML `d0dcbd3f5f9560b47b9af5eea3bb366e5fb44a1d02577a68e6472fa9ee872cd0`。

资源生命周期根 checker 退出 0，`p3-resource-lifecycle-root-n1.json` 绑定 72 组 created/removed 对应收据，SHA256 `e3d573fc87031275b1ede24f4b67c3e83f4af397d8c2c3fc2108b1b9f6a2f0ac`。此项是清理记录核验，不是重新查询服务证明 live absence。完整证据已交 Astra 最终 Spec/Quality 验收；源码尚未提交。本阶段仅证明 synthetic provider 组合下的认证导入 facade，不证明真实嵌入 provider、独立 Worker/API、进程重启或生产切换。

## 2026-10-11：Task03 独立验收通过

Astra 最终 `p3-final-accepted-astra-n4.md` 为 SPEC PASS / QUALITY PASS，SHA256 `75beedac1701b4a03a3a9f2367bb8ca63f65ab7daaad9156dca1afa532733e06`。独立解析原始 XML/日志/pytest 及 wrapper 退出、83 项 before/after 字节与当前源码，确认 41 个完整新文件用例 + 112 个回归用例共 153 个不同用例，全部通过，15 个保护文件未变。S1/S2/T1 问题闭合，精确六文件 Task03 已验收并设 done。根接下来提交/推送授权源码与对应文档；Task04 独立 Worker 仍仅进行只读接口核对，尚无 ready 任务包或实现。
