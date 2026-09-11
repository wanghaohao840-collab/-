# 2026-09-09 受控恢复记录

用户已批准：修复依赖重建 → 核验现场及配对备份 → 恢复旧版。
冻结非必要扩展，沿用现有迁移边界，不清除失败凭据，不重写原清单摘要。

> 2026-09-11 更新：恢复收尾已完成并独立核验。下文原有“尚未完成”描述为当时检查点；以末尾最终核验为当前状态。

## 实施与验收计划

1. 工作树 deploy/learning_release.py 改用 Compose up --no-start --no-deps，保留固定镜像/no-build/pull-never。
   生命周期 fixture 补齐 depends_on；隔离验证更改依赖配置时 Qdrant ID 仍不变且两服务不启动。
2. 生产原发布源码暂不修改（清单绑定其摘要）。保护目录保存一次性恢复驱动；持原运维锁检查
   原清单、journal、未完成 intent、精确新容器 ID、未启动状态、镜像、挂载和卷身份，重新核对配对备份。
   新现场核验凭据是人工批准后的恢复起点，不是原 prepare 的成功凭据。
3. 同锁调用既有配对还原、内容校验、维护探活和旧版放行；App 准备使用修正命令。
   原失败 intent 原样保留，后续 prepare 使用 recovering/recovered 阶段的新凭据。
4. 真实账号在本机交互输入；仅登录成功、固定旧镜像配置持久化且健康后清除维护标记。
   任一步失败保持保护，记录具体阶段，不自动重试数据还原。

替代方案：直接重跑会被未完成凭据拒绝；删除凭据/更新旧摘要会丢失事故证据，因此不采用。
生产进入恢复前镜像为候选 App 与原 Qdrant，两容器 created、StartedAt/FinishedAt 均为零时间。
当前 journal=verified；旧库已升级，不能直接启动旧镜像写入。尚未恢复完成，未提交/推送。

## 执行证据

- 修复已写入工作树，生产原始入口暂不修改，以保留事故清单摘要绑定。
- 专项真实 Docker 验证通过：更改 Qdrant 的目标配置后，使用 up --no-start --no-deps
  只重建 App，Qdrant 完整 inspect 不变，两容器均未启动。1 passed in 21.93s。
  一次性测试项目已清理，不影响生产挂载。
- 现场核验驱动位于本次 learning-release 操作目录 controlled_recovery.py。
  首次只读核验因批准文件数量超过已有上限被拒绝，尚未写 journal 或还原数据；
  收窄为原批准集合加本次驱动与 intent 后，通过既有守卫，未放宽守卫规则。
- INSPECTED_SITE_AND_PAIRED_BACKUP_VERIFIED：核对了原清单、journal、两个精确新 ID、
  created/零启动时间、镜像、App bind/UID、Qdrant 原卷描述与两份备份摘要及 metadata。
  只读核验结果独立写入 inspected-recovery.json，原失败 intent 没有新增 .complete。
- 已打开本机交互恢复终端（PowerShell PID 8468），等待用户本地输入真实账号。
  密码不写命令行、发布清单或日志。最终成功必须出现 CONTROLLED_RECOVERY_OK recovered，
  且 controlled-recovery-complete.json、固定旧镜像配置、健康状态和维护标记清除独立核实。
  尚不能宣称恢复成功。原 App/Qdrant 配对冷备份保留。

## 2026-09-11 最终恢复核验

- 本机交互收尾入口已完成真实应用账号登录校验，生成 `terminal-release-complete.json`；未保存账号密码。
- 独立核对：凭据 phase 与 journal 均为 `recovered`，journal 摘要和当前两个容器凭据完全匹配。
- `deploy-state/maintenance.json` 已清除；`compose.release.yaml` 与本次 `rollback.yaml` SHA-256 一致，固定旧 App/Qdrant 镜像。
- App/Qdrant 均 running/healthy、正常模式；`/healthz` 和 `/login` 返回 200，App 仅绑定 127.0.0.1:7860。
- 配对 App/Qdrant 备份摘要仍与 journal 一致；原失败 intent 和准备凭据未改写。
- 配置摘要差异已确定为 Docker 重启将空 Dns/DnsOptions/DnsSearch 从 null 表示为 []；仅枚举这三项空值表示即可精确复现旧摘要。非空 DNS 或其他字段变化仍被拒绝；38 项容器观察回归通过。
- 最终本机核验输出保存在忽略的 `.runtime/recovery-final-verification.json`。此结果仅确认旧版恢复完成，不代表新整合提交已部署。
- 本次恢复修正与记录尚未提交、未推送；产品整合提交保持独立。
