# 固定镜像学习功能发布

> 2026-09-11：旧版恢复收尾已完成，详见 `learning-recovery-20260909.md` 最终核验。
> 下列原始清单及 run/recover 命令仅作历史记录，不得重跑已终结的恢复操作。

本节是历史迁移记录，不能作为当前生产状态。当前工作台发布见 `2026-09-12-workspaces-release.md`。
当前数据库已经包含 learning 表，**不得使用本文 run 命令再次执行旧库升级**。
常规前端更新不需要数据库迁移；复现当前应用应使用 Git 中 Dockerfile、当前源码和独立部署 env。
固定镜像部署的备份恢复使用配对归档，先在隔离环境演练。

历史恢复时生产运行固定旧版 App/Qdrant，维护标记已清除，最终完成凭据已核验。
新版本发布必须建立新的发布操作和验证证据；本记录不表示整合提交已上线。

## 本次已准备清单

`D:\python_self_agent\deploy-state\learning-release\f54bb870-b91e-4fd4-9e38-24b9f27efe5c\manifest.json`

prepare 不停止服务、不升级数据库。run 会持锁进入维护、停止 App/Qdrant、
创建配对冷备份、在候选容器中离线升级、核对原表数据、维护探活、正常启动、
真实登录及学习接口校验，最后持久化固定镜像配置并清除维护标记。
已有输入或容器身份变化时拒绝运行，不应手工改清单绕过。

## 在本机终端安全执行

在 PowerShell 中执行以下命令；用户名为现有真实账号，密码输入不回显，
只临时传给当前终端及其 Python 子进程，不写入发布清单；结束后恢复原环境。
不要在聊天中发送密码。

```powershell
Set-Location D:\python_self_agent
$env:PYTHON_DOTENV_DISABLED = '1'
$releaseCredential = Get-Credential -Message 'Existing application login for release verification'
$previousReleaseUsername = $env:ZHIYAN_RELEASE_USERNAME
$previousReleasePassword = $env:ZHIYAN_RELEASE_PASSWORD
try {
    if ($null -eq $releaseCredential) { throw 'Login credential required' }
    $env:ZHIYAN_RELEASE_USERNAME = $releaseCredential.UserName
    $env:ZHIYAN_RELEASE_PASSWORD = $releaseCredential.GetNetworkCredential().Password
    .\venv\Scripts\python.exe -m deploy.learning_release run D:/python_self_agent/deploy-state/learning-release/f54bb870-b91e-4fd4-9e38-24b9f27efe5c/manifest.json
    if ($LASTEXITCODE -ne 0) { throw 'Release failed; inspect maintenance journal before recovery' }
} finally {
    $env:ZHIYAN_RELEASE_USERNAME = $previousReleaseUsername
    $env:ZHIYAN_RELEASE_PASSWORD = $previousReleasePassword
    $releaseCredential = $null
}
```

成功必须出现 `RELEASE_OK complete`，并确认生产健康、镜像为候选、
`compose.release.yaml` 已落盘且 maintenance.json 已清除。
登录失败等异常会保留维护保护并停止已验证目标；不能直接调用旧启动脚本放行。

若失败，先检查日志与 journal，再用同一清单显式 recover。
上面命令的 `learning_release run` 改成 `learning_release recover`，保持同一清单，
仍需真实账号。
终态 complete/recovered 的恢复不会再次还原数据；未完成的容器准备凭据会拒绝
自动恢复并要求检查。不要删除维护标记、编辑 journal 或重新 prepare 绕过恢复。

## 固定配置与回退

正式成功后，运维模块优先选择 compose.release.yaml。登录恢复、健康自愈
显式使用该文件，固定模式禁止构建和拉取；旧 Update-Deployment.ps1 拒绝
固定模式重新构建。原始 compose.yaml 保留不覆盖。

回退必须使用配对冷备份与原镜像，不能只换镜像后继续写入升级后的数据库。
本次候选 App：
`sha256:99f2e8b9758a8a79e9704432067fca70c8d0662460ceab63c650203abfca0636`。
旧 App：
`sha256:f40ddd4d17f6a2fa820ec0dfad83cd8ebc5d80f929d13dcbdf1e4a5dc1689d2e`。
Qdrant：
`sha256:59f9f7fb8494896adbb0dc63fae731dfa0cdd564a40af45222b30ff6e5c3f21f`。

6 个被更新运维文件的原内容快照保存在
`D:\python_self_agent\deploy-state\release-entry-sync-20260909-before\deploy\windows`。
不要在迁移中途还原这些脚本。原业务源码、数据、环境文件和原始 Compose 未覆盖。
