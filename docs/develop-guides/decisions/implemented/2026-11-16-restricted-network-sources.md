# 受限网络部署的镜像与包源

状态：implemented
类型：feature
Owner：docker-compose.yml

## 问题

默认部署在构建期直接访问 GitHub Release、Debian 官方 apt 源、官方 PyPI、npm registry 和 Alpine CDN。只拥有 Docker registry 镜像源、apt 源与 PyPI 源的内网环境无法完成构建：`docker/minio/Dockerfile` 的 `apk add` 需要 Alpine CDN，`docker/api.Dockerfile` 与 `docker/sandbox.Dockerfile` 的 apt 与 pip 需要外网，`docker/frontend.Dockerfile` 需要 npm 或 pnpm，而 `scripts/pull_image.sh` 把镜像源主机名写死。

同时存在一个不显然的约束：`backend/uv.lock` 把每个 registry 包的下载地址钉成官方 `files.pythonhosted.org` 的绝对 URL，仅在构建期注入索引变量不会改变实际下载地址，因此更换 PyPI 源必须重新生成锁文件。

## 决策

### 实现方案

- 拉取镜像：新增 `scripts/pull_image_for_mirror.sh` 与 `scripts/pull_image_for_mirror.ps1`，逻辑与 `scripts/pull_image.sh`、`scripts/pull_image.ps1` 相同，主机名前缀改由 `YUXI_IMAGE_MIRROR` 提供，默认仍是 `m.daocloud.io`；镜像源缺少镜像时回落原仓库。上游脚本不改，避免内网用户与 CI 的默认行为分叉。
- 构建参数：`docker/api.Dockerfile` 增加 `ARG APT_MIRROR`（默认值等于原先硬编码的清华源）与 `ARG PYPI_INDEX_URL`（默认空）。`PYPI_INDEX_URL` 非空时先执行 `UV_DEFAULT_INDEX="$PYPI_INDEX_URL" uv lock --no-cache`，再执行原有的 `uv sync --frozen`；为空时行为与原先逐字节相同。`docker/sandbox.Dockerfile` 增加同名两个 `ARG`，apt 换源只在 `APT_MIRROR` 非空时执行，并同时改写 `debian.sources` 里的 `deb.debian.org` 与 `security.debian.org/debian-security` 两节；pip 索引只在该 RUN 内 `export PIP_INDEX_URL`，不写入运行期环境。
- 前端：`docker/frontend.Dockerfile` 增加 `prebuilt` 阶段，只复制宿主机构建好的 `frontend/dist`，镜像内不执行 npm 或 pnpm。该阶段放在 `production` 之前，保证不带 `--target` 时默认目标仍是 `production`。切换方式是 compose 变量 `YUXI_FRONTEND_BUILD_TARGET`（默认 `production`），`.dockerignore` 相应不再忽略 `frontend/dist/`。
- MinIO：新增 `docker/minio/Dockerfile.mirror` 与 `docker-compose.mirror.yml` 叠层。镜像副本不装任何包：使用者把 `minio.linux-<arch>.<版本>` 预上传到宿主目录，叠层通过 `additional_contexts: minio-binary` 把它作为命名构建上下文传入，Dockerfile 直接 `COPY` 并保留原有 sha256 校验；健康检查改用基础镜像自带的 busybox wget（不再安装 curl）。上游 `docker/minio/Dockerfile` 与两份 compose 的 minio 段不改。
- 参数传递：两份 compose 用 `x-api-build` anchor 统一 api、worker、app-init、sandbox-runner 的 `build` 块并传入两个 `ARG`，`sandbox-image` 只在开发 compose 单独传参（生产沙盒镜像由宿主机预构建）。

默认值全部等于改动前的行为：不设置任何变量时镜像内容、healthcheck 与拉取结果不变。

## 替代方案

- 在 `scripts/pull_image.sh` 内部读环境变量：与「新增文件优先」的维护约定冲突，且让 CI 依赖的脚本承担内网分支。
- 只注入 `UV_INDEX_URL`/`PIP_INDEX_URL` 而不重新 lock：锁文件里的绝对下载地址仍指向官方域名，安装照旧失败且没有报错提示。
- 复制 `docker/api.Dockerfile` 等大型 Dockerfile 作为副本：上游每次调整依赖或构建步骤都会静默漂移，冲突反而不出现，问题延后到构建期。
- 修改 `daemon.json` 或 `.env` 里的全局 registry 配置：影响本机其他项目，且与 Compose 的构建期参数无关。
- MinIO 副本改为从内网 HTTP 地址下载二进制：内网环境通常没有可用的静态文件服务，且需要额外暴露一个服务；预上传到构建机目录不需要新增任何运行组件。
- MinIO 改用 Debian 基础镜像以便复用 apt 源：镜像体积增加约 40MB，并把原本零包依赖的镜像重新绑回包管理器。

## 后果

内网用户可以在只有 Docker registry 镜像源、apt 源与 PyPI 源的条件下构建 api、sandbox、frontend 与 minio。三类代价被显式接受：使用 `PYPI_INDEX_URL` 时镜像内 `uv.lock` 由镜像源重新求解，版本可能偏离仓库锁文件；`frontend` 使用 `prebuilt` 后镜像不再自证前端构建，需要宿主机构建产物；minio 需要人工上传约 97MB 的二进制，且文件名必须与版本、架构匹配，替换版本时要同时更新叠层目录里的文件。mineru、cube 与 Skills 的 `npx -y` 仍依赖外网或预构建镜像，未提供参数。

## 验证

- `docker compose -f docker-compose.yml config --quiet` 通过；prod 文件需 `YUXI_ENV_FILE` 与必填密钥变量才通过，两者在设置 `YUXI_FRONTEND_BUILD_TARGET=prebuilt` 时解析出的 frontend `build.target` 为 `prebuilt`，`APT_MIRROR`、`PYPI_INDEX_URL` 正确展开到四处 build args。
- `docker compose -f <prod 或 dev> -f docker-compose.mirror.yml config` 解析出的 minio `build.context` 仍为 `./docker/minio`，`build.dockerfile` 为 `Dockerfile.mirror`，`additional_contexts` 指向 `MINIO_BINARY_DIR`，healthcheck 为 wget。
- `backend/test/unit/config/test_mirror_sources.py` 用假 docker 记录实际 pull 的仓库串，覆盖默认 host、尾斜杠、单级与多级路径补全，以及镜像源失败后回落原仓库；并对两份 compose、四个 build 块、`sandbox-image`、frontend target、Dockerfile 默认值、沙盒两节 apt 换源与 `.dockerignore` 做静态断言。
- 容器内完整 `pytest test/unit -m "not slow"`：3896 passed / 12 skipped / 1 failed。失败项 `test_python_package_sources.py` 的官方源守卫由“在只读挂载的临时副本里按镜像索引重新 lock”触发，仓库 `backend/uv.lock` 未被修改。`scripts/ci/verify_engineering_contracts.py` 与其 56 个 unittest 通过。
- 真实构建验证：`sandbox-image`（apt 与 pip 均走镜像站，镜像内 `PIP_INDEX_URL` 未固化）、api 镜像（镜像内 `/app/uv.lock` 全部指向镜像站、`files.pythonhosted.org` 为 0）、前端 `--target prebuilt`（镜像内无 npm 或 pnpm）、minio 叠层（预上传二进制，healthcheck 报 healthy）。
- 未验证：`scripts/pull_image_for_mirror.ps1` 未在 PowerShell 下执行，只按 sh 版本对照审查；Windows 宿主上的 `pnpm build` 与 `MSYS_NO_PATHCONV` 提示未实测。
