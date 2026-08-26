# Windows 单节点生产化运维设计

**日期：** 2026-08-09  
**状态：** 已完成分段设计确认，待书面规格审阅

## 1. 背景

项目已经具备单节点 Docker Compose 部署：默认运行一个 Gradio `app`
容器和一个 Qdrant 容器，可按需启用 Neo4j `graph` Profile；只有应用端口
发布到宿主机。现有部署还提供容器健康检查、默认/深度冒烟检查，以及面向
Linux 主机的冷备份和恢复脚本。

当前实际部署运行在 Windows 11 Home 与 Docker Desktop 上，Docker Desktop
数据已经迁移到 D 盘。应用与 Qdrant 可正常启动并通过冒烟检查，但 Windows
宿主机尚无受版本控制的自动恢复、计划备份、备份保留、恢复演练、防火墙
收口、日志轮转、桌面告警和可回滚升级工具。

本设计为当前 Windows 机器增加一个仓库内、可审计、可卸载的单机运维包。
访问边界为可信内网，不支持公网直连。

## 2. 已确认决策

- 目标主机：当前 Windows 机器与 Docker Desktop。
- 网络范围：可信内网；应用端口为 TCP 7860。
- 自动启动：Windows 用户登录后恢复，不要求无人登录时运行。
- 恢复目标：登录后 3 分钟内使默认 Compose 服务恢复健康。
- 备份目录：`D:\python_self_agent_backups`。
- 备份计划：每天 03:00 执行冷备份。
- 保留策略：最近 7 个日备份和 4 个周备份。
- 恢复演练：每月在隔离环境自动执行一次。
- 升级方式：人工触发一键升级，失败自动回滚；不自动拉取 Git 代码。
- 告警方式：日志、状态文件和已登录桌面的 Windows 通知。

## 3. 目标

- 提供可重复安装和卸载的 Windows 运维配置。
- 登录后等待 Docker Engine，并在 3 分钟内启动和验证 Compose 服务。
- 每 5 分钟检查 Docker、容器健康和应用 HTTP 状态，并执行一次受控恢复。
- 每日生成完整冷备份、SHA-256 和不含密钥的元数据。
- 安全执行 7 日备份、4 周备份保留策略。
- 每月在隔离数据目录和隔离 Compose 项目中验证最新备份可恢复。
- 仅向可信本地子网开放应用端口；不开放 Qdrant 或 Neo4j。
- 限制 Docker 和运维日志增长，并提供去重的桌面告警。
- 提供带预备份、镜像保存、漏洞扫描、深度冒烟和失败回滚的一键升级。
- 在 CI 中验证部署契约、构建镜像并执行漏洞扫描。
- 保持现有单副本限制、认证、用户隔离、`document_id` 隔离和存储契约。

## 4. 非目标

- 无人登录时启动 Docker Desktop。
- 将 Docker Desktop 替换为 WSL 内独立 Docker Engine。
- Kubernetes、Swarm、多节点、多副本或多 worker 部署。
- 公网访问、域名、HTTPS 证书或反向代理。
- 自动执行 `git pull`、自动切换分支或清理未提交文件。
- 无人值守自动升级。
- 热备份或零停机备份。
- 自动删除升级前备份、回滚镜像或恢复失败诊断数据。
- 修改认证、Session、Memory、RAG、Storage 或业务数据格式。

## 5. 方案选择

### 5.1 采用：PowerShell 与 Windows 任务计划程序

运维实现放在仓库的 `deploy/windows/` 中，使用 Windows PowerShell 和系统
任务计划程序。该方案不增加第三方进程守护依赖，能复用当前 Docker Desktop
登录会话，并且配置可测试、可审计、可卸载。

### 5.2 未采用：WinSW 或 NSSM

第三方服务包装器能提供更强的进程监管，但 Docker Desktop 仍依赖登录会话；
引入额外安装、更新和权限边界不能解决本项目的核心问题。

### 5.3 未采用：WSL systemd 与 cron

该方案更接近 Linux 服务器，但需要改变 Docker Engine 的所有权和启动模型，
与已确认的“登录后使用 Docker Desktop 恢复”边界不符。

## 6. 总体架构

新增的 Windows 运维包包含以下独立组件：

1. 安装/卸载：创建或移除计划任务、防火墙规则、状态目录和安全 ACL。
2. 登录恢复：等待 Docker Engine、启动 Compose、等待健康并执行默认冒烟检查。
3. 健康巡检：每 5 分钟检查关键状态并最多尝试一次受控恢复。
4. 备份与保留：执行 Windows 原生冷备份并维护日/周保留集合。
5. 恢复演练：使用隔离目录、隔离项目名和隔离端口验证最新备份。
6. 日志与通知：集中日志、轮转、状态 JSON、故障去重和桌面通知。
7. 发布升级：预检、测试、预备份、候选构建、扫描、验收和回滚。

所有脚本都从明确的仓库根、环境文件和状态根开始解析绝对路径。脚本不得
依赖调用者的当前目录，不得把密钥写入命令输出、报告或备份。

## 7. 目录与文件边界

计划新增或修改的主要范围为：

```text
deploy/windows/
├── Install-Operations.ps1
├── Uninstall-Operations.ps1
├── Start-Deployment.ps1
├── Test-DeploymentHealth.ps1
├── Backup-Deployment.ps1
├── Invoke-RestoreDrill.ps1
├── Update-Deployment.ps1
├── Operations.Common.psm1
└── README.md

deploy-state/
├── logs/
├── reports/
└── status.json

D:\python_self_agent_backups\
├── daily/
└── weekly/
```

`deploy-state/` 是本机运行状态，不提交 Git。安装脚本会创建它并验证其解析后
路径仍位于仓库根目录。备份根位于仓库和 `DEPLOY_DATA_ROOT` 之外。

实现还会更新 Compose 日志配置、环境变量模板、部署文档、部署专项测试和
GitHub Actions 工作流。不会修改业务层模块。

## 8. 自动启动与健康巡检

### 8.1 登录恢复任务

登录触发任务只对当前用户注册，并在交互登录后运行：

1. 最多等待 Docker Engine 180 秒，使用有界重试，不启动第二个 Docker Desktop。
2. 运行 `docker compose --env-file deploy/.env up -d`。
3. 等待默认 `app` 和 `qdrant` 达到 running/healthy。
4. 运行默认 `deploy/smoke_test.py`，不调用 LLM。
5. 写入状态 JSON 和操作日志；失败时发送一次通知。

任务使用“已有实例仍在运行时不启动新实例”策略。超时后退出非零，不循环
重建容器，不删除数据，也不自动恢复备份。

全部任务使用当前用户的交互登录令牌，不存储 Windows 密码。定时任务启用
`StartWhenAvailable`，错过计划时间后在用户仍处于登录状态且机器恢复可用时
补跑；每日备份与每月演练允许唤醒计算机，但不会在无人登录时启动 Docker
Desktop。

### 8.2 周期健康任务

健康任务每 5 分钟运行，检查：

- Docker Engine 可连接；
- Compose 的 `app` 与 `qdrant` 存在并为 running/healthy；
- `http://127.0.0.1:7860` 在明确超时内响应。

首次失败时只运行一次 `docker compose up -d` 并重新检查。再次失败则退出非零、
记录诊断和发送去重通知。健康任务不执行备份恢复、镜像重建或递归重启。

### 8.3 月度触发器的 Task Scheduler CIM 兼容性（真实主机决策）

2026-08-15 的 Windows 11 Home / Windows PowerShell 5.1 真实主机验收采用
Task Scheduler CIM 兼容方案。安装脚本必须用 `Get-CimClass` 从
`Root/Microsoft/Windows/TaskScheduler` 解析名称精确等于
`MSFT_TaskMonthlyDOWTrigger` 的类，再把该 `CimClass` 传给一次
`New-CimInstance -CimClass ... -ClientOnly -Property ...` 调用。不得改用只按
`-ClassName` 创建的空实例。生成的对象必须物化继承的触发器属性，运行时类仍精确
为 `MSFT_TaskMonthlyDOWTrigger`，并包含 ScheduledTasks 参数绑定要求的
`Microsoft.Management.Infrastructure.CimInstance#MSFT_TaskTrigger` 类型身份；
任一条件不满足都在系统写入前失败。

月份掩码字段只接受以下两个大小写精确的架构名称之一：Task Scheduler 文档使用的
`MonthsOfYear`，或本机 CIM 架构实际暴露的兼容名称 `MonthOfYear`。解析结果必须
恰好命中一个名称；两者都没有、两者同时存在、名称不精确、类型不可赋值或其他
歧义都 fail-closed，不推测别名。属性表在同一次 `New-CimInstance` 调用中设置并在
返回后逐项断言：`Enabled = $true`；`StartBoundary` 为安装当日本地时间
`04:00:00` 的 ISO 8601 秒精度值，作为不晚于后续有效触发时间的下界与时刻锚点；
`DaysOfWeek = [uint16]1`（星期日）；`WeeksOfMonth = [uint16]1`（第一个星期）；
以及实际解析出的月份字段为 `[uint16]4095`（全年十二个月）。第一个星期日由日、
周和月份掩码共同定义，不用代码近似计算下一次日期；由此保持每月第一个星期日
04:00 的现有语义。

安装脚本必须在创建状态目录、修改环境文件 ACL、防火墙或任务计划程序之前，完成
当前 WTS 活动会话用户的 `Interactive` / `Highest` principal、四个 action、全部
trigger、settings 和四个内存任务定义的构建与验证。月度定义必须通过
`New-ScheduledTask` 的内存参数绑定，且验证后的触发器类型、字段和值与上述断言
一致；这一验证不得注册任务。只有四个定义全部有效后才能进入既有写入阶段，实际
注册仍使用当前 WTS principal，不改变登录恢复、5 分钟健康、每日 03:00 或月度
演练的其他语义。

验证矩阵必须覆盖：

| 架构/场景 | 预期结果 |
|---|---|
| 仅有 `MonthsOfYear` 且继承属性与类型完整 | 通过；写入文档字段，精确掩码为 `1 / 1 / 4095`，`Enabled` 为 true |
| 仅有 `MonthOfYear` 且继承属性与类型完整 | 通过；写入本机兼容字段，其他断言完全相同 |
| 两个月份字段都缺失或同时存在 | 在任何系统写入前拒绝 |
| `Enabled` 或其他必需字段未物化、类型不可赋值、值回读不一致 | 在任何系统写入前拒绝 |
| 对象缺少精确派生类或继承的 `MSFT_TaskTrigger` 类型身份 | 在任何系统写入前拒绝 |
| Windows 11 Home / PowerShell 5.1 真实安装 | 安装脚本连续运行两次均成功；只有四个精确任务、一个精确防火墙规则，月度任务仍为第一个星期日 04:00 |

不采用每周触发器近似月度计划，因为固定周间隔不能表达“每月第一个星期日”，会随
月份长度漂移。也不采用直接 Task Scheduler COM API，因为它会绕过当前统一的
ScheduledTasks action、principal、settings 和注册路径，迫使月度任务维护第二套
对象与错误处理语义。该决定只修复 CIM 架构兼容性，不放宽 principal、幂等性或
fail-closed 边界。

#### 8.3.1 月度任务注册路径的真实主机决策协议

2026-08-15 的真实主机安装证明：四个内存任务定义均可构造且外层
`ShouldProcess` 均为 true，但 `Register-ScheduledTask -InputObject` 对最后一个
月度定义在每次安装中都触发一次
`PS_ScheduledTask::RegisterByObject` / `0x80041001`，最终只持久化三个任务。
由于该生成式 ScheduledTasks 函数的非终止错误没有被当前调用提升，安装脚本仍然
输出完成，包装器也错误写入两次通过。此状态不是成功，也不得仅凭命令返回或包装器
标记接受安装。

在再次修改生产注册路径或重试安装前，必须先执行一个独立、临时、可证明清理的月度
canary。canary 名称只能是单一精确白名单前缀
`PythonSelfAgent-Canary-MonthlyRestoreDrill-` 加密码学随机的 32 位小写十六进制后缀；
后缀必须来自 `RandomNumberGenerator` 填充的 16 个新字节，不得使用 `Random`、时间戳
或 GUID 文本代替。生成后必须再次按该精确语法校验，不得接受调用方给出的任意名称、
通配符或前缀删除。
canary 必须复用已经验证的月度 trigger、`Invoke-RestoreDrill.ps1` action、settings，
以及从当前活动 WTS 会话解析出的同一用户。principal 必须显式构造为该 WTS 用户的
`Interactive` / `Highest` 等价语义。canary 只验证定义与持久化，不得启动任务，
因此不得执行恢复演练、访问备份数据或改变应用数据。

canary 仅测试 ScheduledTasks 的 `RegisterByPrincipal` 参数路径：

```powershell
$registered = @(Register-ScheduledTask `
    -TaskName $canaryName `
    -Action $validatedMonthlyAction `
    -Trigger $validatedMonthlyTrigger `
    -Settings $validatedMonthlySettings `
    -Principal $validatedWtsPrincipal `
    -Force -ErrorAction Stop)
```

调用前后不得创建或修改防火墙规则、环境文件 ACL、状态数据、备份或生产任务。
命令必须返回恰好一个非空对象；随后必须通过 ScheduledTasks provider 和
`schtasks`/精确任务 XML 独立重查根路径下恰好一个同名 canary。持久化定义必须逐项
验证：`ScheduleByMonthDayOfWeek`、星期日、第一周、全年十二个月、本地 04:00:00，
同一 WTS principal 的 `InteractiveToken` / `HighestAvailable`，可信的精确 action
脚本与参数，以及既定 `StartWhenAvailable`、`WakeToRun`、`IgnoreNew` settings。
任何缺失、重复、路径错误、值漂移、空输出或非终止错误都算 canary 失败。

清理必须置于 `finally`，且只能在名称仍满足本次随机 canary 的精确值和白名单语法、
provider 重查也只返回该一个对象时，调用
`Unregister-ScheduledTask -TaskName $canaryName -Confirm:$false -ErrorAction Stop`。
随后必须用 provider 与 `schtasks` 双重证明该精确 canary 已不存在。不得枚举前缀后
批量删除；清理失败本身是阻断性失败，必须报告并由人工处理，不能继续生产安装。

若 canary 的注册、完整持久化验证和 finally 清理全部成功，生产月度任务可以采用该
最小 `RegisterByPrincipal` 参数路径；其余三个任务继续使用现有的已验证路径。若
canary 返回 `0x80041001`、无法持久化，或不能保持完全相同的 principal/trigger/
action/settings 语义，则不得改变任何生产任务，设计退回到一个另行评审的“仅月度任务
使用文档化 Task Scheduler XML”方案。该 fallback 必须表达真正的
`ScheduleByMonthDayOfWeek`，仍使用同一 WTS principal/action/settings，并经过同样的
精确重查；本节只定义 fallback 边界，不授权直接实现它，也不恢复已拒绝的 COM 注册
双路径。

无论最终采用哪条注册路径，每一次 ScheduledTasks/NetSecurity 写调用都必须显式
`-ErrorAction Stop`，检查命令结果基数与身份，并立即重查持久化对象。安装成功输出前
还必须独立要求：四个精确任务各一个且无额外 `PythonSelfAgent-*` 对象；每个任务的
action、trigger、principal、settings 与合同一致；精确防火墙规则只有一个并且仍为
Enabled / Inbound / Allow / TCP 7860 / Private / LocalSubnet；环境文件 ACL 仍为受保护
的三个预期非继承主体。包装器只有在安装脚本返回 0 且上述最终状态全部成立时才能写
`success`。这项要求必须单独验证 wrapper marker truthfulness。三任务或其他部分状态
必须返回失败、写真实失败标记并原地保留，以便后续
幂等 `-Force` 收敛；不得把部分状态清理成不可诊断状态，也不得再次 false-success。

验证矩阵至少包括：

| 场景 | 预期结果 |
|---|---|
| 随机 canary 经 `RegisterByPrincipal` 返回一个对象，provider/XML 全部精确，finally 双重证明已删除 | 允许单独评审生产月度任务采用该参数路径；生产状态在 canary 阶段不变 |
| canary 复现 WMI `RegisterByPrincipal`/`RegisterByObject` `0x80041001`、产生非终止错误或零输出 | 提升为失败；finally 只清理精确 canary；不进行生产写入；进入 XML fallback 设计评审 |
| canary 存在但 XML 的首个星期日、04:00、全年月份、principal、action 或 settings 任一不符 | 失败并精确清理；不得接受“接近”等价语义 |
| canary 清理失败或清理后仍能由任一查询路径找到 | 阻断并报告精确名称；不得继续生产安装或扩大删除范围 |
| 生产注册调用返回对象但重查缺少任一任务、名称/路径重复或只有三个任务 | 安装非零失败；包装器写失败而非 success；保留可幂等恢复的部分状态 |
| Windows 11 Home / PowerShell 5.1 连续两次安装 | 每次均显式捕获 provider 错误并验证四任务、一规则、精确 ACL；第二次无重复；包装器标记与真实状态一致 |

继续拒绝每周触发器近似“每月第一个星期日”，因为月份长度会造成漂移。也拒绝在脚本
内部计算下一个日期、注册一次性任务后自行重排的方案；它引入跨月状态、自修改与崩溃
恢复语义，不能替代 Task Scheduler 原生月度合同。

#### 8.3.2 人为部分状态恢复与隐藏窗口执行

2026-08-25 的复核确认，`PythonSelfAgent-LoginRecovery` 被用户主动删除，
`PythonSelfAgent-Health` 与 `PythonSelfAgent-DailyBackup` 被用户主动禁用；这不是
canary 或安装脚本造成的漂移。此后 canary 的生产不变性门禁不得假定开始时必有三个
任务。它必须先对当时实际存在的精确 `PythonSelfAgent-*` 对象逐个导出 XML 并记录
名称、状态与哈希，同时记录防火墙规则、环境文件字节与 ACL、备份根、Docker 容器
身份与健康状态以及网络配置。canary 结束后，这一完整快照必须逐项相同，且随机
canary 仍须经 provider 与 `schtasks` 双重证明不存在。快照只用于证明 canary 没有
越界修改，不能把缺失或禁用的生产任务认定为安装成功。

canary 通过后的生产安装负责从零个、两个、三个或四个目标任务的部分状态幂等收敛。
成功条件仍是根路径下恰好四个精确任务、无额外 `PythonSelfAgent-*` 对象，且四个任务
全部启用。每个持久化 XML 都必须明确验证任务级 `Settings/Enabled=true`；只检查
provider 对象存在或触发器的 `Enabled` 字段不够。第二次安装必须保持相同四任务、
一条防火墙规则和精确 ACL，不能产生重复对象。

四个任务继续使用当前活动 WTS 用户、`Interactive` 与 `Highest`，以便访问同一用户
会话中的 Docker Desktop；不改为 S4U、服务账户或“无论用户是否登录都运行”。每个
任务的 `powershell.exe` action 在既有 `-NoProfile -NonInteractive -ExecutionPolicy
Bypass -File ...` 参数中加入 `-WindowStyle Hidden`，默认在后台运行且不持续弹出控制台
窗口。Task Scheduler 的任务可见性设置不用于替代该参数，因为隐藏任务本身不能保证
隐藏 PowerShell 窗口。日志、状态文件和既有失败报告仍保留；本决定只改变交互窗口，
不吞掉非零退出码，也不放宽安装器的持久化验证或通知合同。

测试必须覆盖：任意合法部分状态经一次安装收敛为四个已启用任务；第二次安装保持
幂等；任一任务持久化为 disabled 时安装失败且包装器不得写 success；四个 action 均
包含且仅包含一个 `-WindowStyle Hidden`；不存在 S4U、保存密码、服务账户或仅设置任务
`Hidden` 的替代实现。真实主机验收还必须独立导出四个任务 XML，验证任务级 Enabled、
隐藏窗口参数、既定 action/trigger/principal/settings，并手动启动 LoginRecovery 与
Health，确认无控制台弹窗且默认冒烟检查仍通过。

#### 8.3.3 月度任务的受控 XML fallback

2026-08-25 的隔离 canary 已证明本机 `RegisterByPrincipal` 也不能接受验证后的月度
CIM trigger：唯一一次提升运行返回 `CimException / InvalidArgument`、零注册输出和
exit 51；finally 清理与 provider/`schtasks` 双重不存在证明均成功，生产快照完全不变。
因此不得重试 `RegisterByObject` 或 `RegisterByPrincipal`，月度任务进入本节预留的
XML fallback。其他三个任务继续使用现有 `-InputObject` 路径；只有
`PythonSelfAgent-MonthlyRestoreDrill` 使用 `Register-ScheduledTask -Xml`。

安装器新增一个单一职责构造器：

```powershell
New-OperationsMonthlyRestoreDrillXml `
  -Task <validated task definition> `
  -Principal <validated active-WTS principal> `
  -> [string]
```

构造器必须使用 `System.Xml.XmlWriter` 或 `XmlDocument` 节点 API 和 Task Scheduler
命名空间 `http://schemas.microsoft.com/windows/2004/02/mit/task`，不得通过字符串拼接
或模板替换组装 XML。所有动态值在进入构造器前已由现有路径合同验证；用户主体必须
解析为当前活动 WTS 身份的 SID，action 只能来自已经验证的单个
`New-OperationsTaskAction` 对象。XML 节点 API 负责转义 action 参数，不允许 XML
片段、任意 task name、额外 action 或调用方提供的 principal/settings 文本。

XML 的固定语义为：schema version `1.4`；根路径任务；一个 `CalendarTrigger`，其
`StartBoundary` 为安装当日本地 04:00:00、`Enabled=true`，并包含一个
`ScheduleByMonthDayOfWeek`：`Weeks/Week=1`、`DaysOfWeek/Sunday`、January 至
December 各一次。一个 principal 使用当前 WTS SID、`InteractiveToken` 和
`HighestAvailable`。一个 `Exec` action 使用 `powershell.exe`，参数与验证后的 action
完全一致并含且仅含一个 `-WindowStyle Hidden`。settings 必须表达
`MultipleInstancesPolicy=IgnoreNew`、`StartWhenAvailable=true`、`WakeToRun=true`、
`Enabled=true`、`Hidden=false`、`AllowStartOnDemand=true`、`ExecutionTimeLimit=PT72H`
和 `Priority=7`；同时固定
`DisallowStartIfOnBatteries=true`、`StopIfGoingOnBatteries=true`、
`AllowHardTerminate=true`、`RunOnlyIfIdle=false`、
`RunOnlyIfNetworkAvailable=false`、`DisallowStartOnRemoteAppSession=false`、
`UseUnifiedSchedulingEngine=false`，以及 IdleSettings 的 `Duration=PT10M`、
`WaitTimeout=PT1H`、`StopOnIdleEnd=true`、`RestartOnIdle=false`。不写空的过期删除、
重启间隔或维护设置。
`Hidden=false` 保持任务可审计；窗口隐藏仅由 action 参数实现。

月度任务必须显式写入 `UseUnifiedSchedulingEngine=false`，不得省略后仅依赖 schema
默认值，也不得沿用其他三个任务的 `true`。微软 Task Scheduler 合同明确将 Monthly 与
Monthly day-of-week trigger 列为 unified scheduling engine 不支持的功能；不兼容组合
会在注册时被拒绝（[What’s New in Task Scheduler](https://learn.microsoft.com/en-us/windows/win32/taskschd/what-s-new-in-task-scheduler)、
[UseUnifiedSchedulingEngine](https://learn.microsoft.com/en-us/windows/win32/taskschd/taskschedulerschema-useunifiedschedulingengine-settingstype-element)）。
该兼容性例外只属于月度 XML，不改变 LoginRecovery、Health 和
DailyBackup 的既有 setting。

注册调用只有这一种形式：

```powershell
$result = @(Register-ScheduledTask `
  -TaskName 'PythonSelfAgent-MonthlyRestoreDrill' `
  -TaskPath '\' -Xml $monthlyXml -Force -ErrorAction Stop)
```

调用必须返回恰好一个非空对象，且其名称和根路径精确匹配。随后立即使用
`Get-ScheduledTask`、`Export-ScheduledTask` 和 `schtasks /Query /XML` 三路重查。
两份持久化 XML 按语义而非字节比较：首个星期日、04:00、全年月份、SID、
InteractiveToken、HighestAvailable、单一 action、隐藏窗口参数、Enabled 和全部
settings 必须相同。provider 的 task-level `Settings.Enabled` 也必须为 true。
任何 native stderr、非零退出码、空/多输出、缺失节点、重复节点或值漂移都提升为
安装失败；不得仅因 `Register-ScheduledTask` 返回而宣布成功。

语义比较必须同时验证命名空间和精确子节点集合，不能只比较 `LocalName`。月度
`CalendarTrigger` 只能包含 `StartBoundary`、`Enabled` 与
`ScheduleByMonthDayOfWeek`；后者只能包含一个 Weeks、DaysOfWeek 和 Months，且各自
只能含规定的 Week/Sunday/十二个月节点。必须拒绝错误 namespace、`EndBoundary`、
`RandomDelay`、`Repetition`、trigger-level `ExecutionTimeLimit` 或任何未知 trigger
子项。Principal 只能包含 `UserId`、`LogonType`、`RunLevel`，不得出现
`RequiredPrivileges`、`ProcessTokenSidType` 或未知子项。单一 Exec 只能包含 Command
与 Arguments，不得出现 `WorkingDirectory` 或额外 action 节点。Settings 与
IdleSettings 同样必须是本节固定节点集合；缺失、重复和额外节点都失败。

三个非月度任务的 provider 与导出 XML 也必须按各自定义验证完整有效语义，而非只看
trigger 类型或少数字段。共同 settings 包括 Enabled、Hidden、StartWhenAvailable、
MultipleInstances、battery、idle、network、on-demand、ExecutionTimeLimit、Priority、
remote-app 与 unified-engine；XML 省略 schema 默认节点时先按微软文档默认值归一化，
再与期望对象比较。LoginRecovery 精确验证单一 LogonTrigger 及 UserId/Delay，并拒绝
EndBoundary、RandomDelay、ExecutionTimeLimit 等额外语义；Health 精确验证单一每日
CalendarTrigger、00:00、PT5M/P1D repetition；DailyBackup 精确验证单一每日 03:00
CalendarTrigger。每种任务都要有独立的 trigger/settings 漂移负例。

生产安装仍然先构造并验证四个内存任务定义，再开始任何系统写入。月度 XML 也必须在
该写边界前构造、重新解析并完成同一组语义断言。注册后复用最终四任务/一规则/精确
ACL 断言；从当前两个禁用任务状态幂等收敛，失败时保留可诊断的部分状态，不删除已
存在的有效任务、防火墙或 ACL。包装器只有在连续两次安装均返回 0 且独立 postflight
验证四个已启用任务后才能写 `success`。

TDD 至少覆盖：XML 构造器正确生成全部固定节点；特殊字符仅作为转义后的 action
参数文本；缺失/重复月份、错误星期/周次/时间、非当前 SID、错误 LogonType/RunLevel、
额外 action、缺失或重复 `-WindowStyle Hidden`、`Enabled=false`、错误 WakeToRun 或
MultipleInstances 均在写前/重查时失败；月度仅调用 `-Xml` 而其他三个仅调用
`-InputObject`；provider 非终止错误被提升；零/多输出失败；从零、两个禁用、三个或
四个合法部分状态收敛为四个已启用任务；第二次安装不产生重复。禁止 COM、
`schtasks /Create`、每周近似、一次性自重排和任何新的 canary 重试。
另须覆盖月度 `UseUnifiedSchedulingEngine=true`、省略该节点、错误 namespace、额外
EndBoundary/RandomDelay/Repetition/WorkingDirectory/principal 子项，以及三个非月度
任务各自的完整 trigger/settings 漂移；这些用例都必须在 fake provider 中可执行地
失败，而不是只做源码字符串断言。

## 9. 备份、保留与恢复演练

### 9.1 Windows 冷备份

PowerShell 备份与现有 Linux `backup.sh` 保持相同安全契约：

1. 验证环境文件、数据根和备份根的绝对路径。
2. 拒绝空路径、驱动器根、仓库根、数据根内部的备份目录和路径穿越。
3. 记录当前运行的 Compose 服务。
4. 停止当前运行服务。
5. 使用 Windows 自带 `tar.exe` 归档完整数据根。
6. 使用 `Get-FileHash -Algorithm SHA256` 生成校验文件。
7. 写入创建时间、Git revision、服务名和镜像 ID；不写环境内容。
8. 在 `finally` 中恢复原先运行的服务。

归档命名使用 UTC 时间和固定前缀。归档不包含 `deploy/.env`，因为环境文件不在
数据根内。备份失败不会进入保留清理阶段。

### 9.2 日/周保留

每日备份写入 `daily/`。成功后按时间从新到旧保留 7 组，每组由归档、校验和
元数据构成。每周从成功的日备份中选择一个生成或复制到 `weekly/`，保留最新
4 组。每周副本取星期日 03:00 任务产生的成功备份；如果该任务补跑，则仍按
归档的 UTC 创建时间归入对应自然周。周副本是独立文件组，日备份淘汰不会使
周备份失效。

清理只接受固定命名格式，并在删除每个文件前重新解析路径，确认目标直属于
`daily/` 或 `weekly/`。任何意外文件、链接、重解析点或越界路径都会使清理
中止。清理不使用宽泛 glob 对未知路径做递归删除。

### 9.3 每月恢复演练

恢复演练在每月第一个星期日 04:00 运行，使用最新成功备份，且不替换正式数据：

1. 验证 SHA-256。
2. 检查归档成员，拒绝绝对路径、`..`、符号链接和硬链接。
3. 解压到 D 盘临时隔离目录，验证 `app/`、`qdrant/` 和可选 `neo4j/` 结构。
4. 创建受限 ACL 的临时环境文件，覆盖数据根、应用端口和 Compose 项目名。
5. 启动隔离的 `app` 与 `qdrant`，运行默认冒烟检查。
6. 关闭并移除演练容器，删除临时环境文件。
7. 成功时清理演练数据；失败时保留不含密钥的诊断目录和报告。

演练不得连接或修改正式 Compose 项目、正式数据目录或正式应用端口。临时环境
文件内容不得进入日志。

## 10. 网络与密钥安全

### 10.1 防火墙

安装脚本仅在当前网络类别为 `Private` 时创建入站规则。规则限制为：

- TCP；
- 本地端口 7860；
- Profile 为 Private；
- RemoteAddress 为 LocalSubnet；
- 不适用于 Public 或 Domain Profile。

若当前活动网络不是 Private，安装过程不改变网络类别，也不开放端口；它会
明确失败并给出人工处理提示。Qdrant 6333/6334 和 Neo4j 7474/7687 不创建
防火墙规则，Compose 继续不发布这些端口。

卸载只移除由本运维包创建且名称精确匹配的规则。

#### 10.1.1 Docker Desktop 监听器兼容性（真实主机决策）

2026-08-14 的真实主机验收采用选项 A。对于 TCP 7860，当且仅当 Compose
恰好解析出一个预期的 `app` 容器，且该容器的 `PortBindings` 包含空地址或
`0.0.0.0` 的通配 HostIP 绑定时，同端口的环回监听器可与该通配绑定视为地址兼容。
兼容集合必须同时包含至少一个 `0.0.0.0` 或 `::` 通配监听器；集合中的每个
监听器 PID 都必须可解析、不得为 System PID，并且进程身份仍属于现有已接受的
Docker Desktop 转发器集合。判定始终锚定到这个唯一 `app` 容器及其
`PortBindings`，不得借此接受其他容器、端口或新的转发器身份。只有环回监听器、
已接受与未知进程混合、无法解析 PID、System PID 或非转发器监听器均继续
fail-closed。

当前函数把 `::` 作为空地址或 `0.0.0.0` HostIP 对应的通配监听地址，但不把
HostIP `::` 本身识别为通配绑定；本决策不扩展该 IPv6 HostIP 语义。若后续明确
支持 IPv6 通配 HostIP，必须以同等的唯一容器、完整监听器集合和 fail-closed
测试另行证明，不能由本决策隐式放宽。

未采用把 Compose 绑定改为 DHCP 分配的 LAN 地址，因为该地址会随网络和租约
变化，使部署配置依赖易变的主机状态。也未采用要求运维人员或宿主机额外创建
通配监听器的规避方案：Docker Desktop 已为预期映射提供通配转发器，额外监听器
会扩大进程与端口边界、引入冲突，并掩盖实际转发拓扑。此决策不改变既有
Private/LocalSubnet 防火墙范围或产品暴露面。

验证必须覆盖：合成的单一通配绑定加已接受通配/环回转发器通过；仅环回集合、
混合或未知身份、System PID、不可解析 PID、缺少或存在多个预期 `app` 容器的
情况均被拒绝；最后在真实 Docker Desktop 主机上重复运行安装脚本，并验证
幂等任务、防火墙和 ACL 后置条件。

### 10.2 环境文件

`deploy/.env` 继续被 Git 忽略。安装脚本将其 ACL 限制为当前用户、SYSTEM 和
Administrators。ACL 操作前先验证路径准确指向仓库内的 `deploy/.env`，并保留
当前用户的读取和修改权限，使 Docker Compose 能继续使用该文件。

日志函数屏蔽名称包含 `KEY`、`TOKEN`、`PASSWORD`、`SECRET` 的值，并清理 URL
用户信息。脚本不打印完整环境、进程环境块或临时环境文件。

## 11. 日志、状态与通知

Compose 的 `app`、`qdrant` 和可选 `neo4j` 使用有大小上限的 Docker 本地日志
配置，避免容器日志无限增长。

运维日志位于 `deploy-state/logs/`。单个日志达到 10 MB 时轮转，最多保留 7 份。
`deploy-state/status.json` 记录最后检查时间、总体状态、各检查结果、最后成功
备份和最近错误类别，不记录命令行、密钥或环境值。

以下事件发送 Windows 桌面通知：

- 登录恢复失败；
- 持续健康失败；
- 每日备份失败；
- 每月恢复演练失败；
- 升级失败并进入回滚；
- 先前故障恢复健康。

相同故障类别 30 分钟内最多通知一次。通知是最佳努力行为；通知 API 失败不会
覆盖主任务的退出码，操作日志和任务计划历史是权威记录。

## 12. 一键升级与回滚

`Update-Deployment.ps1` 只升级当前工作区内容，不运行 Git 写操作。流程为：

1. 验证 Docker、Compose、环境文件、磁盘空间和当前部署状态。
2. 检查工作区差异并在报告中记录；不清理或覆盖任何文件。
3. 运行部署专项测试和 `docker compose config`。
4. 运行升级前冷备份，并要求备份、校验和元数据完整。
5. 记录当前应用/Qdrant 镜像 ID并创建本地回滚标签。
6. 构建候选镜像并执行漏洞扫描。
7. 启动候选部署，等待容器健康，运行默认和深度冒烟检查。
8. 成功后写入升级报告并保留升级前备份。
9. 失败时恢复旧镜像标签；若候选部署已启动并可能写入数据，则使用升级前备份
   恢复数据，然后验证旧部署健康。

回滚失败时停止继续变更、保留诊断数据并发出高优先级通知。脚本不得在无法
证明备份有效时替换正式数据。

## 13. 镜像固定、扫描与 CI

- Python、Qdrant 和 Neo4j 基础镜像使用版本标签加不可变 digest。
- digest 更新是显式代码变更，必须经过构建和部署验证。
- 本机升级使用 Docker Scout 扫描候选镜像；扫描器不可用或扫描失败会阻止升级。
- 存在有修复版本的 Critical 漏洞会阻止升级，并在报告中列出镜像和漏洞数量。
- CI 使用固定版本的扫描工具作为独立强制检查，避免依赖开发机插件状态。
- CI 运行部署专项 pytest、Compose 配置校验、镜像构建和漏洞扫描。
- CI 不使用真实 `deploy/.env` 或真实 LLM 密钥，深度冒烟只在本机升级验收中运行。

## 14. 安装与卸载

安装脚本需要管理员权限来创建防火墙规则；若没有权限则在任何系统写入前停止，
并给出重新以管理员身份运行的命令。它按以下顺序执行：

1. 只读预检路径、Docker、网络类别、环境文件和端口占用。
2. 创建状态与日志目录。
3. 收紧环境文件 ACL。
4. 创建精确命名的防火墙规则。
5. 注册登录恢复、5 分钟健康、每日备份和每月演练任务。
6. 手动触发并验证登录恢复和健康任务。
7. 写入安装报告。

安装操作应幂等；重复执行会更新本运维包自己的任务和规则，不创建重复项。

卸载脚本只删除精确命名的任务和防火墙规则。它不删除 `deploy/.env`、正式数据、
备份、状态日志、容器、镜像或 Docker Desktop 数据。卸载后报告剩余数据位置。

## 15. 错误处理与安全原则

- 所有外部命令都检查退出码，并使用参数数组避免字符串拼接执行。
- 计划任务采用单实例策略和明确超时。
- 恢复与升级使用阶段状态，失败后只执行与已完成阶段对应的补偿动作。
- 任何无法解析或超出预期根目录的路径都会导致 fail-closed。
- 不使用 `docker compose down --volumes`、`git reset --hard` 或递归删除正式数据。
- 不自动清理用户未提交修改、回滚目录、升级前备份或失败诊断目录。
- 对 Docker 暂时不可用采用有界重试；对数据校验失败不重试写操作。

## 16. 测试与验收

### 16.1 自动测试

- PowerShell 模块纯函数测试：绝对路径边界、备份分组、保留选择、脱敏、告警冷却。
- 脚本契约测试：任务名称/触发器、单实例、端口/Profile/LocalSubnet、防火墙卸载范围。
- Compose 契约测试：日志限制、只有应用发布端口、基础镜像固定 digest。
- 升级测试：预检失败不修改状态、扫描失败阻止部署、冒烟失败进入镜像和数据回滚。
- 现有 `tests/deploy` 与完整项目回归测试。

### 16.2 本机集成验收

- 安装脚本重复运行不产生重复任务或防火墙规则。
- 手动触发登录恢复任务后，默认服务在 3 分钟内 healthy，默认冒烟通过。
- 健康任务成功写入状态；模拟不可达后只尝试一次恢复并产生去重通知。
- 每日备份生成归档、SHA-256 和元数据，服务自动恢复健康。
- 构造隔离测试备份后，月度恢复演练通过且不改变正式数据。
- 防火墙只存在 Private/LocalSubnet/TCP 7860 规则。
- Qdrant 与 Neo4j 端口没有宿主机映射。
- 一键升级完成候选构建、扫描和深度冒烟；故障注入验证回滚分支。
- Docker 与运维日志均受到大小和保留数量限制。

### 16.3 人工最终验收

- 从同一可信内网的另一台设备打开 `http://<本机内网IP>:7860`。
- 验证 6333、6334、7474 和 7687 无法从内网访问。
- 执行一次真实 Windows 重启并登录，确认 3 分钟内服务恢复健康。
- 检查一次桌面测试通知。

## 17. 完成标准

以下条件全部满足才算完成：

- Windows 运维包、文档和测试已提交，且不包含密钥或运行数据。
- 四个计划任务已注册并可成功手动执行。
- 每日备份与日/周保留策略在隔离样本上验证。
- 月度恢复演练通过，正式数据未被替换。
- 防火墙只允许 Private LocalSubnet 访问应用端口。
- 容器和运维日志轮转生效，桌面通知测试成功。
- 基础镜像由 digest 固定，CI 构建与漏洞扫描配置有效。
- 一键升级成功路径和故障回滚路径均通过验证。
- 默认及深度冒烟通过，app 与 Qdrant healthy。
- 真实重启后登录恢复满足 3 分钟目标，或明确记录为唯一需要用户执行的最终验收。
- 未修改或提交用户现有的 GraphRAG 任务包变更与 `deploy/.env`。
