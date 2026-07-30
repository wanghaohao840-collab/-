# 单节点 Docker 部署设计

**日期：** 2026-07-30  
**状态：** 已完成分段设计确认，待书面规格审阅

## 1. 背景

当前多用户文档学习助手以 `ui/gradio_app.py` 作为 Web 入口，默认把
Gradio 固定监听在 `127.0.0.1:7860`。应用数据可通过
`PDF_ASSISTANT_DATA_DIR` 外置，RAG 可在 JSON 与 Qdrant 之间切换，Neo4j
文档图谱由环境变量选择性启用。仓库尚无应用镜像、Compose 服务编排、
容器健康检查、部署冒烟测试及整栈备份恢复工具。

本设计为一台 Linux 云主机或内网服务器提供可重复部署方案。首版通过
服务器 IP 和 HTTP 访问；默认运行应用与 Qdrant，Neo4j 作为可选
Compose Profile。

## 2. 目标

- 使用一条 Compose 启动命令构建并启动应用和 Qdrant。
- 保持本地开发默认监听 `127.0.0.1:7860`，容器部署时监听
  `0.0.0.0:7860`。
- 只把应用端口发布到宿主机，Qdrant 和 Neo4j 仅在内部网络可访问。
- 将应用、Qdrant 和可选 Neo4j 数据持久化到宿主机可备份目录。
- 提供不调用 LLM 的默认冒烟检查，以及使用临时隔离数据的深度冒烟检查。
- 提供具有校验、失败恢复和原数据保留能力的整栈冷备份与恢复命令。
- 保持现有用户隔离、`document_id` 隔离、来源追踪和损坏数据
  fail-closed 规则。
- 用部署专项测试、Compose 配置验证、镜像构建和运行验收证明部署可用。

## 3. 非目标

- Kubernetes、Swarm、多节点或多副本部署。
- 多进程或多 worker Gradio。
- 公网直接暴露、自动申请证书或内置 HTTPS 反向代理。
- JSON 与 Qdrant 之间的数据迁移或双写。
- GraphRAG 问答融合或图谱 UI。
- 无网络环境中的离线镜像与 Python 依赖分发。
- 生产环境零停机备份。

## 4. 约束与假设

- 目标主机运行 Linux、Docker Engine 和 Docker Compose v2。
- 目标 CPU 架构必须受所选 Python、Qdrant 和 Neo4j 镜像支持。
- 首版只允许一个 `app` 容器副本。Session、用户锁和部分协调状态仍在
  进程内，横向扩容会破坏现有并发与会话保证。
- LLM 可由目标主机访问的外部或内网 OpenAI 兼容接口提供。部署方案不绑定
  特定模型供应商。
- 内网 HTTP 端口必须由宿主机防火墙限制在可信网段。任何公网部署都必须在
  Compose 之外增加 HTTPS 网关。
- 容器镜像使用 Python 3.11、`qdrant/qdrant:v1.18.2` 和
  `neo4j:5.26.28-community`。应用继续使用仓库中的 `requirements.txt`。

## 5. 总体架构

### 5.1 服务

`compose.yaml` 定义三个服务：

1. `app`
   - 从仓库根目录的 `Dockerfile` 构建。
   - 单进程执行 `python ui/gradio_app.py`。
   - 容器内固定监听 `0.0.0.0:7860`。
   - 以非 root UID 运行。
   - 通过内部服务名 `qdrant` 访问向量服务。
   - 只把 7860 映射到配置的宿主机地址与端口。

2. `qdrant`
   - 默认与应用一起启动。
   - 使用 `qdrant/qdrant:v1.18.2`。
   - 只声明内部端口，不发布 6333 或 6334 到宿主机。
   - readiness 通过后，Compose 才启动应用。

3. `neo4j`
   - 使用 `neo4j:5.26.28-community`。
   - 属于 `graph` Profile，默认不启动。
   - 只在 Compose 内部网络暴露 Bolt 与 HTTP 端口。
   - 启用前必须设置非示例密码及对应的应用连接变量。
   - 应用不把 Neo4j 作为硬启动依赖；图谱故障继续遵循现有弱一致性规则。

三个服务位于同一条用户自定义 bridge 网络。该网络不设置
`internal: true`，因为应用仍需访问 LLM；服务隔离依靠不发布 Qdrant 和
Neo4j 端口实现。只有 `app` 存在宿主机端口映射。

### 5.2 应用依赖边界

容器化不改变现有依赖方向：

```text
Browser
  -> Gradio UI
  -> Session / UserRuntime
  -> PDFLearningAssistant
  -> RAGTool / MemoryTool
  -> SQLite / JSON / Qdrant / optional Neo4j
```

部署配置只进入 UI 启动层和现有环境变量接口。Memory、RAG 和 Storage
不得反向依赖部署脚本、UI 或 Assistant。

## 6. 镜像设计

`Dockerfile` 使用 `python:3.11-slim-bookworm`，完成以下动作：

- 设置 `PYTHONDONTWRITEBYTECODE=1`、`PYTHONUNBUFFERED=1` 和
  `PIP_NO_CACHE_DIR=1`。
- 先复制并安装 `requirements.txt`，再复制项目源文件，以利用构建缓存。
- 创建固定非 root 应用用户以及 `/app/data`。
- 将仓库根目录设为 `/app`，确保实际导入的是镜像中的本地
  `hello_agents`。
- 复制并执行部署 entrypoint。
- 使用镜像内 Python 标准库健康检查脚本，不额外安装 `curl`。
- 不复制 `.env`、宿主机数据、备份、Git 元数据、测试缓存或 Python 缓存。

`deploy/entrypoint.sh` 在启动前：

- 检查 `/app/data` 存在且可写；
- 输出不含凭据的监听地址、数据目录和后端名称；
- 使用 `exec` 启动 Python，使应用进程正确接收停止信号。

## 7. 启动配置

新增一个无 Gradio 副作用的启动配置模块，集中解析以下变量：

| 变量 | 本地默认值 | Compose 值 | 规则 |
|---|---:|---:|---|
| `GRADIO_SERVER_NAME` | `127.0.0.1` | `0.0.0.0` | 非空字符串 |
| `GRADIO_SERVER_PORT` | `7860` | `7860` | 1–65535 的整数 |
| `GRADIO_ROOT_PATH` | 空 | 空 | 可选，以 `/` 开头 |
| `PDF_ASSISTANT_DATA_DIR` | `<project>/data` | `/app/data` | 由现有代码解析 |

`ui/gradio_app.py` 的 `launch()` 只从该模块读取配置，不再硬编码地址和端口。
本地直接执行入口的行为保持不变。

部署专用 `deploy/.env.example` 包含：

- `APP_BIND_ADDRESS=0.0.0.0`
- `APP_PORT=7860`
- `DEPLOY_DATA_ROOT=./deploy-data`
- `LLM_API_KEY`、`LLM_BASE_URL`、`LLM_MODEL_ID`
- 现有 LLM 重试和上下文预算变量
- 空的 `NEO4J_URI` 以及 Neo4j 用户名、密码和数据库示例

Compose 固定注入：

- `PDF_ASSISTANT_DATA_DIR=/app/data`
- `RAG_BACKEND=qdrant`
- `QDRANT_URL=http://qdrant:6333`
- `GRADIO_SERVER_NAME=0.0.0.0`
- `GRADIO_SERVER_PORT=7860`

真实 `deploy/.env` 被 Git 忽略。镜像构建、日志、健康检查和备份均不得输出
API Key、Neo4j 密码或含凭据 URL。

## 8. 持久化

默认宿主机目录为 `${DEPLOY_DATA_ROOT:-./deploy-data}`：

```text
deploy-data/
├── app/
│   ├── app.db
│   ├── uploads/
│   └── users/
├── qdrant/
└── neo4j/
    └── data/
```

- `app` 将 `deploy-data/app` 绑定到 `/app/data`。
- `qdrant` 将 `deploy-data/qdrant` 绑定到 `/qdrant/storage`。
- `neo4j` Profile 将 `deploy-data/neo4j/data` 绑定到 `/data`。
- 部署文档在首次启动前创建目录并设置容器所需权限。
- 容器删除、重新创建或升级不得删除这些宿主机目录。

## 9. 启动与关闭

首次部署：

```sh
cp deploy/.env.example deploy/.env
# 编辑 deploy/.env，至少设置可用的 LLM 配置。
docker compose --env-file deploy/.env up -d --build
```

启用图谱：

```sh
# 先在 deploy/.env 中设置 NEO4J_URI、NEO4J_USERNAME、
# NEO4J_PASSWORD 和 NEO4J_DATABASE。
docker compose --env-file deploy/.env --profile graph up -d
```

关闭容器但保留数据：

```sh
docker compose --env-file deploy/.env down
```

部署文档不得把 `down --volumes` 作为常规命令。

## 10. 健康检查与错误处理

### 10.1 容器健康

- Qdrant healthcheck 使用其 HTTP readiness 接口。
- Neo4j healthcheck 使用镜像内的 `cypher-shell` 和部署凭据。
- 应用 healthcheck 使用 Python 标准库请求本机 Gradio HTTP 地址。
- `app` 通过 `depends_on: condition: service_healthy` 等待 Qdrant。
- 服务统一使用 `restart: unless-stopped`，但健康检查本身不删除或重建数据。

应用 HTTP 健康只证明 Web 进程可响应，不调用 LLM，也不创建用户。外部 LLM
不可用不会把登录页标记为不健康，相关业务操作仍返回现有的脱敏错误。

### 10.2 启动失败

- 数据目录不可写时，entrypoint 在启动 Gradio 前以非零状态退出。
- 端口配置无效时，启动配置模块给出不含密钥的明确错误。
- Qdrant 未就绪时，Compose 不启动应用。
- Neo4j 未启用或故障时，主 RAG 链路仍可工作；图谱状态按现有规则记录失败并
  允许重试。

## 11. 冒烟检查

`deploy/smoke_test.py` 提供两个模式。

### 11.1 默认模式

默认模式不调用 LLM、不写入业务用户数据，检查：

- 应用容器和 Qdrant 容器处于 healthy；
- Gradio 根页面及配置端点可访问；
- Qdrant readiness 可访问；
- `/app/data` 可创建、读取并删除一个随机命名的临时文件；
- `hello_agents.__file__` 位于 `/app/hello_agents/`。

任一检查失败时返回非零退出码，并输出不含凭据的检查名称和原因。

### 11.2 深度模式

深度模式在应用容器中：

- 使用临时 SQLite 和用户数据目录；
- 生成唯一用户、唯一 RAG namespace 和测试 TXT；
- 通过现有 Session、Assistant 和 Tool 边界导入测试文档；
- 向配置的 LLM 提出一条只依赖测试文本的问题；
- 验证回答成功且检索来源属于测试文档；
- 在 `finally` 中删除测试 namespace 的 Qdrant 数据并移除临时目录。

深度模式不接触正式 `app.db`、正式用户目录或固定 namespace。没有有效 LLM
配置时明确失败，不降级成伪成功。

## 12. 备份与恢复

### 12.1 备份

`deploy/backup.sh` 执行整栈冷备份：

1. 解析明确传入的部署环境文件和数据根目录。
2. 记录当前正在运行的 Compose 服务。
3. 停止应用、Qdrant 和已启用的 Neo4j。
4. 将完整数据根目录归档到部署数据目录之外的 `backups/`。
5. 写入备份时间、服务列表、镜像标签和源代码版本元数据。
6. 为归档生成 SHA-256 校验文件。
7. 通过 shell `trap` 在成功或失败时恢复原先运行的服务。

备份包不包含 `deploy/.env` 或其他密钥文件。脚本拒绝把备份输出目录放在数据
根目录内部，避免递归归档。

### 12.2 恢复

`deploy/restore.sh <archive>` 执行：

1. 校验 SHA-256。
2. 拒绝包含绝对路径、`..` 路径段或非预期顶层目录的归档。
3. 将归档解压到数据根目录的同级 staging 目录。
4. 记录当前运行服务并停止整栈。
5. 将当前数据根目录重命名为带时间戳的 rollback 目录。
6. 将 staging 数据目录重命名为正式数据根目录。
7. 恢复原先运行的服务并等待健康检查。
8. 若启动或健康检查失败，停止服务、换回 rollback 目录并再次启动原服务。

恢复脚本不自动删除 rollback 目录。只有操作者验证恢复结果后才能手动清理。

## 13. 文件范围

新增：

- `Dockerfile`
- `.dockerignore`
- `compose.yaml`
- `deploy/.env.example`
- `deploy/entrypoint.sh`
- `deploy/healthcheck.py`
- `deploy/smoke_test.py`
- `deploy/backup.sh`
- `deploy/restore.sh`
- `deploy/README.md`
- `ui/launch_config.py`
- `tests/ui/test_launch_config.py`
- `tests/deploy/test_deployment_contract.py`
- `tests/deploy/test_smoke_test.py`

修改：

- `ui/gradio_app.py`
- `README.md`
- `.gitignore`

不修改现有认证、Session、Assistant、Memory、RAG、Storage 数据契约。

## 14. 测试策略

### 14.1 自动测试

- 启动配置测试覆盖默认值、自定义值、无效端口和无效 root path。
- 部署契约测试检查：
  - 镜像使用非 root 用户；
  - `.dockerignore` 排除密钥和运行数据；
  - Compose 只发布应用端口；
  - Qdrant 是应用的健康依赖；
  - Neo4j 位于 `graph` Profile；
  - 所有持久化目录均指向 `DEPLOY_DATA_ROOT`。
- 冒烟测试单元测试使用本地临时 HTTP 服务和临时目录，不依赖真实 Docker、
  LLM 或正式数据。
- 运行现有完整 pytest 回归，确认用户与文档隔离规则未变化。

### 14.2 部署验证

在有 Docker 的 Linux 环境执行：

```sh
docker compose --env-file deploy/.env config
docker compose --env-file deploy/.env build app
docker compose --env-file deploy/.env up -d
docker compose --env-file deploy/.env ps
docker compose --env-file deploy/.env exec app \
  python deploy/smoke_test.py
docker compose --env-file deploy/.env exec app \
  python deploy/smoke_test.py --deep
```

备份恢复验收在隔离的部署数据目录中完成：

1. 创建测试用户并导入文档。
2. 执行备份。
3. 增加一条备份后数据。
4. 执行恢复。
5. 验证备份前数据存在，备份后数据不存在。
6. 验证应用和 Qdrant 恢复为 healthy。

### 14.3 人工验收

- 从另一台内网机器通过服务器 IP 打开 Gradio。
- 注册、登录、上传 TXT 或 PDF。
- 执行一次问答并核对来源。
- 重启容器后重新登录，确认文档、问答记录和向量仍存在。
- 启用 `graph` Profile 后导入新文档，确认图谱状态可以构建或返回脱敏错误。

## 15. 完成标准

以下条件全部满足才算完成：

- 本地开发启动默认值保持 `127.0.0.1:7860`。
- 默认 Compose 启动后应用和 Qdrant 均为 healthy。
- 可选 `graph` Profile 能启动健康的 Neo4j，且不改变默认服务集。
- 应用是唯一对宿主机发布端口的服务。
- 重建容器后业务数据仍存在。
- 默认和深度冒烟检查按各自契约运行。
- 冷备份、校验、恢复及失败回滚在隔离数据上验证通过。
- 部署专项测试与现有回归测试通过，或对仓库中已存在且与本变更无关的失败
  给出可复现证据。
- 文档明确说明单副本、内网 HTTP、防火墙、密钥和备份限制。
