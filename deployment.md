# 部署配置手册（运维）

给部署/运维人员的操作说明：怎么配、按什么顺序配、配错会怎样。
语义与取舍由[生产部署](docs/advanced/deployment.md)拥有，两者冲突时以它为准；本文只做流程编排。

## 0. 前置检查

| 项 | 要求 |
| --- | --- |
| 运行时 | Docker Engine + Docker Compose v2（`docker compose version` 有输出） |
| 代码 | 仓库根目录，版本 `v0.8.0`，后续命令全部在根目录执行 |
| 端口 | Web `YUXI_FRONTEND_PORT`（默认 80）；API `YUXI_API_PORT`（默认 6050，只绑 `127.0.0.1`） |
| 磁盘 | `YUXI_STATE_DIR`（默认 `./docker/volumes`）放 PostgreSQL、Redis、MinIO、Milvus、Neo4j 数据，以及 `yuxi/threads` 用户文件 |
| 出网 | 见 §3；能直连外网时跳过 §3 |

## 1. 配置流程总览

1. 建环境文件 `.env.prod`（§2）
2. 填必填项，缺一个 Compose 直接拒绝启动（§2.1）
3. 只在受限网络下做：镜像源、apt 源、pypi 源、前端产物、MinIO 二进制（§3）
4. 构建沙盒镜像（§4）
5. 首次启动并验证（§5）

## 2. 环境文件

两种方式，任选其一。**不要混用**：脚本生成后不要再手工 export 同名变量覆盖。

```bash
# 方式 A：初始化脚本生成（会补齐缺失的随机密钥）
export YUXI_ENV_FILE=.env.prod YUXI_ENV=production COMPOSE_PROJECT_NAME=production
export YUXI_STATE_DIR=./.yuxi/production/state YUXI_INSTANCE_ID=Production
export SILICONFLOW_API_KEY='<key>' SANDBOX_BACKEND=container
export SANDBOX_CONTAINER_IMAGE=yuxi-sandbox:0.8.0
docker build -f docker/sandbox.Dockerfile -t "$SANDBOX_CONTAINER_IMAGE" .
export SANDBOX_CONTAINER_IMAGE_DIGEST="$(docker image inspect "$SANDBOX_CONTAINER_IMAGE" --format '{{.Id}}')"
YUXI_INIT_NON_INTERACTIVE=1 ./scripts/init.sh

# 方式 B：手工复制模板
cp .env.template .env.prod && chmod 600 .env.prod
```

初始化脚本当前**无条件要求 `SILICONFLOW_API_KEY`**，即使用别家模型也必须给值，之后在「智能体 → 模型供应商」换成自己的。

### 2.1 必填项

**Compose 强制校验**，缺失直接报 `Set <VAR> in .env.prod` 并拒绝启动：

| 变量 | 取值 |
| --- | --- |
| `JWT_SECRET_KEY` | `openssl rand -hex 32` |
| `API_KEY_DERIVATION_SECRET` | 再生成一个，**必须与 JWT 不同** |
| `YUXI_INSTANCE_ID` | 稳定实例名，重建保持不变 |
| `POSTGRES_PASSWORD` | 强密码 |
| `NEO4J_PASSWORD` | 强密码 |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | 强凭据 |

**运行期校验**（Compose 能起来，但对应能力会明确报错或不可用）：

| 变量 | 取值 | 缺失后果 |
| --- | --- | --- |
| `SANDBOX_RUNNER_TOKEN` | 独立随机 32+ 字符 | container 后端启动即报「至少 16 个字符」 |
| `SANDBOX_BACKEND` | `container`（单租户可信代码，默认）或 `cube`（强隔离，另配 `CUBE_*`） | 默认取 `container` |
| `SANDBOX_CONTAINER_IMAGE` / `SANDBOX_CONTAINER_IMAGE_DIGEST` | 预构建沙盒镜像名与镜像 ID，见 §4 | runner 拒绝启动 |
| `YUXI_ENCRYPTION_KEY` | 仅使用 MCP OAuth 持久凭据时必须；Fernet 密钥，生成：`python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'` | 调用该能力时报「必须配置为持久 Fernet 密钥」 |

### 2.2 建议显式设置

```dotenv
YUXI_ENV=production
COMPOSE_PROJECT_NAME=production        # 小写字母/数字/连字符/下划线
YUXI_STATE_DIR=./.yuxi/production/state
YUXI_VERSION=0.8.0                     # 镜像标签
YUXI_FRONTEND_PORT=80
YUXI_API_PORT=6050
YUXI_PUBLIC_URL=https://yuxi.example.com
YUXI_CORS_ORIGINS=                     # 只有跨域浏览器访问才填，逗号分隔；生产默认禁止跨域
YUXI_KNOWLEDGE_ENABLED=true
TZ=Asia/Shanghai
```

### 2.3 三条硬规则

- 插值与注入必须是同一份文件：命令行 `--env-file .env.prod` 与 `YUXI_ENV_FILE=.env.prod` 同时给。Compose 的 `env_file` 只注入容器，不负责插值。
- 密钥只生成一次并且持久保存。重建时保留原值；脚本遇到无效的非空密钥会明确失败，**不会自动轮换**。JWT 与 API Key 派生密钥不能相同，也不能有首尾空白。
- `COMPOSE_PROJECT_NAME` + `YUXI_STATE_DIR` 决定「这套环境」。换项目名前先用旧配置 `docker compose down`（不加 `-v`）保留数据。

## 3. 受限网络（内网 / 只有镜像源）

把下面这些填进 `.env.prod` 或构建时以 `--build-arg` 传入，未设置时行为与联网构建一致。

| 变量 | 作用 | 说明 |
| --- | --- | --- |
| `APT_MIRROR` | api / sandbox 镜像的 Debian 源 | 只填主机名，如 `mirrors.tuna.tsinghua.edu.cn`；该主机必须同时提供 `/debian` 与 `/debian-security` |
| `PYPI_INDEX_URL` | Python 包索引 | 非空时 api 镜像在构建期按该索引重新 `uv lock`，之后的 `uv sync --frozen` 走新锁 |
| `YUXI_IMAGE_MIRROR` | 拉基础镜像的前缀式镜像源 | 默认 `m.daocloud.io`；用法 `YUXI_IMAGE_MIRROR=<host> bash scripts/pull_image_for_mirror.sh <image>`，缺镜像时回落原仓库 |
| `YUXI_FRONTEND_BUILD_TARGET` | 前端构建方式 | `prebuilt` = 不跑 npm，直接打包宿主 `frontend/dist`（生产 Compose 生效） |
| `MINIO_BINARY_DIR` | MinIO 二进制所在目录 | 与 `docker-compose.mirror.yml` 叠加使用，默认 `./docker/minio/dist` |

需要提前知道的三件事：

1. **pypi 源会改变锁定版本**。镜像内 `uv.lock` 由该索引重新解析，可能与仓库锁文件不同；要核对就用 `docker run --rm --entrypoint cat <api 镜像> /app/uv.lock`。内网索引必须 HTTPS，只能明文 HTTP 时在对应 RUN 里自行 export `UV_INSECURE_HOST` / `PIP_TRUSTED_HOST`。
2. **MinIO 二进制要人工上传**：按 `minio.linux-<TARGETARCH>.<MINIO_VERSION>` 命名放进 `MINIO_BINARY_DIR`，构建期用官方 sha256 校验（值见 `docker/minio/Dockerfile.mirror`），篡改或版本不符会直接构建失败。
3. **仍然需要外网的**：自托管 MinerU（`--profile all`，从 modelscope 下权重）、Cube 沙盒（拉 Playwright/Chromium 等）、Agent 运行期 `npx -y` 安装 Skill。纯内网部署要提前预构建或关掉这些能力。

拉镜像的完整顺序（镜像源脚本不跑 `init.sh` 的构建步骤）：

```bash
export YUXI_IMAGE_MIRROR=<镜像源主机>
for image in alpine:3.20 python:3.13-slim node:24-slim node:22-alpine node:24-bullseye-slim \
             milvusdb/milvus:v3.0.2 neo4j:5.26.29 ghcr.io/astral-sh/uv:0.12.6 nginx:alpine \
             postgres:16 redis:7.4.10-alpine; do
  bash scripts/pull_image_for_mirror.sh "$image" || exit 1
done
```

镜像仓库地址**本身**带端口（`harbor.internal:5000/app:1`）时前缀式镜像源表达不了，脚本会回落直连；这类仓库要按仓库配加速（containerd `certs.d/<host:port>/hosts.toml`，或自建 pull-through 缓存），细节见[生产部署](docs/advanced/deployment.md#受限网络环境)。

## 4. 构建沙盒镜像

沙盒是独立镜像，不由 Compose 构建（生产 Compose 只引用它）：

```bash
docker build -f docker/sandbox.Dockerfile -t yuxi-sandbox:0.8.0 .
export SANDBOX_CONTAINER_IMAGE_DIGEST="$(docker image inspect yuxi-sandbox:0.8.0 --format '{{.Id}}')"
```

把镜像名和这个 digest 一起写进 `.env.prod`。换沙盒内容就重新构建、重新取 digest，否则 API 会拒绝用未固定的镜像。

## 5. 首次启动与验证

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container config --quiet
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container build api frontend minio storage-init
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container up -d --no-build

curl --fail http://localhost/api/system/health   # 进程存活
curl --fail http://localhost/api/system/ready    # 可以接流量
```

`ready` 通过后再用浏览器登录并完成一次真实对话。`health`/`ready` 不证明知识库、模型、沙盒可用。

受限网络部署把 §3 的叠加文件带上：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.mirror.yml \
  --profile container build api frontend minio storage-init
docker compose --env-file .env.prod -f docker-compose.prod.yml -f docker-compose.mirror.yml \
  --profile container up -d --no-build
```

## 6. 日常变更

**改环境变量**（端口、模型 Key、开关）：

```bash
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container up -d --force-recreate api worker
```

**换代码版本**：`app-init` 每次部署都要跑，且必须在 API/worker 停止时跑。

```bash
git fetch --tags && git checkout <tag>
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container build api worker app-init frontend
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container stop api worker
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container up --no-deps --force-recreate --exit-code-from app-init app-init
docker compose --env-file .env.prod -f docker-compose.prod.yml --profile container up -d --no-build
```

初始化失败时先看 `logs app-init` 修好再重跑，**不要**直接启动消费者绕过。

**关/开知识库**：叠加 `-f docker-compose.no-knowledge.yml` 关闭；重新开启用不带叠加文件的完整命令。开关不是热切换，需重建 API/worker，切换前停止知识库任务。

**换项目名或迁移数据**：先停任务并等 Run 收敛，用旧配置 `down`（不加 `-v`），再用新配置启动；复用数据时状态目录、实例身份与密钥保持不变。

## 7. 排障速查

| 症状 | 先看 |
| --- | --- |
| `Set <VAR> in .env.prod` | §2.1 必填项；确认 `--env-file` 指向的文件就是 `.env.prod` |
| 构建卡在 apt / pip / npm | 网络不一致：确认 `APT_MIRROR`、`PYPI_INDEX_URL` 已传成 `--build-arg`，前端用 `YUXI_FRONTEND_BUILD_TARGET=prebuilt` |
| 构建期 `sha256sum` 校验失败 | MinIO 二进制与 `MINIO_VERSION` 不匹配，或文件损坏 |
| `storage-init` 非 0 退出 | 状态目录只读、宿主文件系统不允许改属主、UID 映射不匹配 |
| `/api/system/ready` 不通过 | PostgreSQL/Redis 是否健康；worker 心跳是否新鲜（`logs worker`） |
| 前端 404 / 空白页 | 是否用了 `prebuilt` 但没放 `frontend/dist`；nginx 配置来自 `docker/nginx` |
| 沙盒起不来 | digest 未固定、`SANDBOX_RUNNER_TOKEN` 为空、`sandbox-image` 不存在 |

## 8. 备份与回滚

备份三件套，缺一不可：PostgreSQL（`pg_dump`）、`YUXI_STATE_DIR` 整个目录（含 MinIO/Milvus/Neo4j 数据与 `yuxi/threads` 用户文件）、`.env.prod`。

版本回退要恢复**整套**备份，不能只回退代码：Schema 版本不匹配时初始化器会明确失败，且不提供自动迁移或降级。恢复后保留原有数字属主，再重跑 `app-init` 并启动 API/worker。
