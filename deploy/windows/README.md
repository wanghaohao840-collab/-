# Windows 单机运维

本目录为 Windows 11 + Docker Desktop（Linux/WSL2 后端）提供登录恢复、五分钟
健康巡检、每日一致性冷备份、每月隔离恢复演练，以及人工触发的安全更新和回滚。

Qdrant Dashboard is intentionally unavailable in this deployment. Qdrant HTTP API is the
supported interface，并且只保留在 Compose 私有网络内。派生镜像移除了上游静态 Web UI，
但 this does not make the unmodified upstream image vulnerability-free。

## 网络预检

安装器会拒绝（refus）在活动 Internet 网络为 `Public` 时继续，不会自动修改网络类别。
正式安装前应确认活动网络为 `Private`；入站规则仅允许 `Private` / `LocalSubnet` 的
TCP 7860。公网访问必须另行部署 HTTPS 反向代理与访问控制。

```powershell
Get-NetConnectionProfile | Format-Table InterfaceAlias, InterfaceIndex, NetworkCategory, IPv4Connectivity, IPv6Connectivity
Get-NetFirewallRule -DisplayName 'Python Self Agent - Private Intranet 7860' |
  Format-List DisplayName, Enabled, Direction, Action, Profile
Get-NetFirewallRule -DisplayName 'Python Self Agent - Private Intranet 7860' |
  Get-NetFirewallAddressFilter
```

## 数据边界

- `DEPLOY_DATA_ROOT/app`：SQLite、文档、History、Memory 与报告；
- `DEPLOY_DATA_ROOT/neo4j/data`：启用 `graph` Profile 时的图谱数据；
- `QDRANT_VOLUME_NAME`：Docker Linux 后端中的 Qdrant POSIX 命名卷，默认
  `zhiyan_qdrant_data`；
- `DEPLOY_STATE_ROOT`：健康状态、操作锁、日志和演练报告；
- `DEPLOY_BACKUP_ROOT`：日备、周备、迁移证据和 Qdrant 卷归档；
- `OPERATIONS_PYTHON`：运维 smoke 使用的 Python；稳定部署默认
  `./venv/Scripts/python.exe`，worktree 验证可指向稳定根目录的虚拟环境；
- `deploy/.env`：秘密配置，不进入备份和 Git。

不要把 Qdrant 的 `/qdrant/storage` 重新绑定到 NTFS，也不要直接访问 Docker
Desktop 的内部虚拟磁盘文件。该卷在 Compose 中声明为外部持久卷，因此
`docker compose down --volumes` 不会删除它；全新部署需先运行：

```powershell
docker volume create --label com.zhiyan.role=qdrant-data zhiyan_qdrant_data
```

## 从 NTFS 迁移 Qdrant

先启动 Docker Desktop，确认 `docker version` 成功。预演不会修改卷：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy\windows\Move-QdrantToVolume.ps1 `
  -RepositoryRoot $PWD -EnvFile deploy\.env `
  -LegacyQdrantRoot deploy-data\qdrant -WhatIf
```

执行迁移：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy\windows\Move-QdrantToVolume.ps1 `
  -RepositoryRoot $PWD -EnvFile deploy\.env `
  -LegacyQdrantRoot deploy-data\qdrant
docker volume inspect zhiyan_qdrant_data
```

迁移会保留原目录，并在 `DEPLOY_BACKUP_ROOT/migrations` 生成校验归档和元数据。
只有目标服务健康且集合清单可读取时才报告成功。

## 手工验收

```powershell
Set-Location -LiteralPath 'D:\python_self_agent'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy\windows\Test-DeploymentHealth.ps1 `
  -RepositoryRoot $PWD -EnvFile deploy\.env

powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy\windows\Backup-Deployment.ps1 `
  -RepositoryRoot $PWD -EnvFile deploy\.env

powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy\windows\Invoke-RestoreDrill.ps1 `
  -RepositoryRoot $PWD -EnvFile deploy\.env

powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy\windows\Restore-Deployment.ps1 `
  -Archive 'D:\python_self_agent_backups\daily\assistant-<timestamp>.tar.gz' `
  -RepositoryRoot $PWD -EnvFile deploy\.env
```

新格式备份由宿主机数据归档、Qdrant 卷归档、各自的 SHA-256 和元数据组成。
恢复演练使用独立 Compose project、独立端口、独立宿主机目录和名称以
`zhiyan-drill-` 开头的临时卷；成功后只删除经过名称验证的临时资源。

## 安全更新

更新不会定时自动运行。由维护人员在维护窗口执行：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy\windows\Update-Deployment.ps1 `
  -RepositoryRoot $PWD -EnvFile deploy\.env
```

脚本在构建前创建一致性备份并记录现有镜像，执行 Docker Scout gate、健康检查
和冒烟检查。候选版本失败时恢复原镜像和宿主机/Qdrant 数据；任何回滚失败都会
以高优先级单独报告。

## 安装计划任务

不要从 `.worktrees` 路径安装正式任务。当前分支合并到稳定目录
`D:\python_self_agent` 后，以管理员 PowerShell 执行：

```powershell
Set-Location -LiteralPath 'D:\python_self_agent'
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy\windows\Install-Operations.ps1 `
  -RepositoryRoot D:\python_self_agent -EnvFile deploy\.env
```

安装器注册且只注册：

- `PythonSelfAgent-LoginRecovery`：交互用户登录时恢复部署；
- `PythonSelfAgent-Health`：每五分钟巡检与有限自愈；
- `PythonSelfAgent-DailyBackup`：每天 03:00 一致性冷备份，保留 7 daily 与 4 weekly 备份集；
- `PythonSelfAgent-MonthlyRestoreDrill`：first Sunday 04:00 隔离恢复演练。

检查任务定义与状态，并按需手动触发：

```powershell
Get-ScheduledTask -TaskName 'PythonSelfAgent-*' |
  ForEach-Object { $_; Get-ScheduledTaskInfo -TaskName $_.TaskName }
Start-ScheduledTask -TaskName 'PythonSelfAgent-LoginRecovery'
Start-ScheduledTask -TaskName 'PythonSelfAgent-Health'
Start-ScheduledTask -TaskName 'PythonSelfAgent-DailyBackup'
Start-ScheduledTask -TaskName 'PythonSelfAgent-MonthlyRestoreDrill'
```

默认状态目录为 `D:\python_self_agent\deploy-state`，外置备份目录为
`D:\python_self_agent_backups`。运行日志、最近状态、演练/更新报告分别写入
`operations.log`、`status.json` 与 `reports`；升级报告记录可审计的 `rollback`
镜像和备份归档。调查失败恢复或升级时不要删除这些材料。

查看应用日志：

```powershell
Set-Location -LiteralPath 'D:\python_self_agent'
docker compose --env-file deploy\.env logs --tail 200 app qdrant
```

在 worktree 中只能使用 `-WhatIf` 验证安装器。卸载任务与防火墙规则：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File deploy\windows\Uninstall-Operations.ps1 `
  -RepositoryRoot D:\python_self_agent -EnvFile deploy\.env
```

卸载器 does not delete `deploy\.env`、部署数据、运维状态、备份、容器或镜像。

## LLM 上线门槛

在 `deploy/.env` 配置真实的 `LLM_API_KEY`、`LLM_BASE_URL` 和
`LLM_MODEL_ID` 后，先重新创建应用容器，再执行：

```powershell
docker compose --env-file deploy/.env up -d --force-recreate app
D:\python_self_agent\venv\Scripts\python.exe deploy\smoke_test.py `
  --env-file deploy\.env --deep
```

深度检查会实际调用 LLM；凭据未配置前不要运行。
