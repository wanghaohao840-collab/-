# 单节点 Docker 部署

本目录用于在一台 Linux 云主机或内网服务器上运行单副本文档学习助手。默认
启动 Gradio 应用与 Qdrant；Neo4j 通过 `graph` Profile 按需启动。当前
Session 和用户锁位于进程内，因此必须保持一个应用容器、一个 worker。

## 前置条件

- Linux 主机；
- Docker Engine 和 Docker Compose v2；
- 可访问的外部或内网 OpenAI 兼容 LLM 接口；
- Qdrant 数据必须位于本地或块存储支持的 POSIX 文件系统，不能使用 NFS。

## 首次启动

```sh
cp deploy/.env.example deploy/.env
```

编辑 `deploy/.env`，至少替换：

```dotenv
LLM_API_KEY=replace-with-your-key
LLM_BASE_URL=https://your-llm-endpoint.example/v1
LLM_MODEL_ID=your-model
```

`LLM_BASE_URL` 可以是公司内网的 OpenAI 兼容网关，也可以是服务器可访问的
外部接口。不要提交 `deploy/.env`。

创建宿主机数据目录，并让非 root 应用用户 UID 10001 可以写入应用目录：

```sh
mkdir -p deploy-data/app deploy-data/qdrant
sudo chown -R 10001:10001 deploy-data/app
docker compose --env-file deploy/.env up -d --build
docker compose --env-file deploy/.env ps
python3 deploy/smoke_test.py --env-file deploy/.env
```

默认访问地址为 `http://<服务器内网IP>:7860`。可以在 `deploy/.env` 中通过
`APP_BIND_ADDRESS` 和 `APP_PORT` 调整宿主机监听。直接 HTTP 只适用于可信
内网；请使用服务器防火墙仅允许可信网段。若要公网访问，必须在 Compose
之外增加 HTTPS 反向代理或网关。

## 启用 Neo4j

先在 `deploy/.env` 设置：

```dotenv
NEO4J_URI=neo4j://neo4j:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=使用新的强密码
NEO4J_DATABASE=neo4j
```

然后启动 Profile：

```sh
mkdir -p deploy-data/neo4j/data
docker compose --env-file deploy/.env --profile graph up -d
docker compose --env-file deploy/.env --profile graph ps
```

Neo4j 不参与当前主问答召回。图谱失败不会撤销已经成功的 RAG 导入。

## 冒烟检查

默认检查不调用 LLM，也不创建业务用户：

```sh
python3 deploy/smoke_test.py --env-file deploy/.env
```

上线验收时执行深度检查。它会在临时目录和唯一 Qdrant namespace 中导入
测试 TXT、检索来源并调用一次 LLM，结束后清理测试数据：

```sh
python3 deploy/smoke_test.py --env-file deploy/.env --deep
```

## 日常操作

查看状态和日志：

```sh
docker compose --env-file deploy/.env ps
docker compose --env-file deploy/.env logs --tail 200 app qdrant
```

重启或关闭容器不会删除宿主机数据：

```sh
docker compose --env-file deploy/.env restart
docker compose --env-file deploy/.env down
```

不要把 `docker compose down --volumes` 作为常规操作。

## 冷备份

备份会短暂停止当前运行的应用、Qdrant 和已启用的 Neo4j，以确保 SQLite、
JSON 和数据库文件一致。脚本会在退出时重新启动原先运行的服务。

```sh
sh deploy/backup.sh --env-file deploy/.env
```

默认输出到 `backups/`，包含归档、SHA-256 和不含密钥的元数据文件。
`deploy/.env` 不会进入备份。可以改变备份目录：

```sh
sh deploy/backup.sh \
  --env-file deploy/.env \
  --backup-root /srv/document-assistant-backups
```

备份目录必须位于 `DEPLOY_DATA_ROOT` 之外。

## 恢复

恢复会验证 SHA-256、拒绝路径穿越与链接成员、停止服务，并把当前数据目录
重命名为带时间戳的 rollback 目录。若新数据无法健康启动，脚本会自动换回
原目录；失败的恢复数据和 staging 目录会保留用于排查。

```sh
sh deploy/restore.sh \
  backups/assistant-20260730T120000Z.tar.gz \
  --env-file deploy/.env
```

恢复成功后先登录、检索并检查报告，再手动清理 rollback 目录。脚本不会递归
删除旧数据。

## 数据与秘密边界

- `deploy-data/app`：SQLite、用户文档、History、Memory 和报告；
- `deploy-data/qdrant`：向量数据；
- `deploy-data/neo4j/data`：可选图谱数据；
- `deploy/.env`：LLM Key 与 Neo4j 密码，仅保存在部署主机；
- `backups/`：数据归档和校验文件，不包含 `deploy/.env`。

Qdrant 和 Neo4j 没有宿主机端口映射。应用端口仍应由防火墙限制在可信内网。
