# 决策：sandbox 镜像预装 pandas 与 pyarrow

状态：implemented
类型：feature
Owner：docker/sandbox.Dockerfile

## 问题

容器沙盒默认 Python 环境只预装 openpyxl 等少量库，Agent 在 `execute` 工具里读取表格类文件时缺少 DataFrame 能力：`.xlsx` 虽能靠 openpyxl 打开，但列统计、分组、多表关联都要手写；`.parquet` 则完全没有可用的读写引擎。用户上传或工作区已有的 parquet 文件，Agent 只能报 `ImportError`，无法完成「用 pandas 读取并汇总」这类真实请求。

## 决策

在 `docker/sandbox.Dockerfile` 的 pip 安装列表中加入 `pandas` 与 `pyarrow`，与既有 `openpyxl` 一起构成默认数据文件读写能力。版本不钉死在 Dockerfile 里，沿用该文件现有「构建时取最新兼容版」的做法；实际版本由镜像事实拥有。

### 实现方案

1. `docker/sandbox.Dockerfile` 在 stage 1 的 `pip install --no-cache-dir` 列表加入 `pandas`、`pyarrow`，构建产物仍是单一 `yuxi-sandbox:<version>` 镜像。
2. 沙盒容器以默认 Python 运行 `execute` 命令，直接 `import pandas` / `pyarrow`；Agent 侧不需要新工具、新配置或新 Skill。
3. 镜像 ID 变化后必须同步 `SANDBOX_CONTAINER_IMAGE_DIGEST`（生产初始化与本地 `.env` 都是显式 digest）：`sandbox-runner` 在 `backend/yuxi/sandbox_runner/manager.py` 的 `_verify_image` 中比对，不匹配直接报「sandbox 镜像 digest 与配置不匹配」并拒绝执行。
4. 失败边界不变：依赖缺失、镜像未就绪、digest 不匹配都在 runner 侧显式失败，不降级到宿主 Python。

## 替代方案

- 不预装，由 Agent 在 run 内 `uv pip install pandas pyarrow`：每次 run 增加网络与安装时间，且离线或受限网络下直接失败；保留为将来裁剪镜像体积时的退路。
- 只装 `pandas` 不装 `pyarrow`：`pandas 3.1.1` 的元数据依赖只有 `numpy`、`python-dateutil`，不会带出 pyarrow；`read_parquet` 会报 `ImportError: Unable to find a usable engine`，无法满足 parquet 验收。
- 让用户自建镜像：runner 当前只接受单一镜像，多镜像与模型自建沙盒明确不在范围内（见 `docs/advanced/container-sandbox.md`）。

## 后果

- 镜像体积增加约 40–50 MB（pyarrow wheel 为主），构建时间增加约 40 秒；运行时内存由具体 DataFrame 决定，不改变沙盒资源上限。
- pandas 大版本升级可能改变默认 dtype 与字符串行为，Agent 生成的分析代码以镜像实际版本为准；需要固定版本时在该 Dockerfile 中显式钉版本。
- 每次镜像重建都必须更新部署方的 `SANDBOX_CONTAINER_IMAGE_DIGEST`，否则 runner 拒绝新执行——这是既有的显式校验行为，不是本次新增的耦合。
- 镜像能力文档同步：`docs/advanced/container-sandbox.md` 的镜像说明点名数据类库。

## 验证

- 构建：`docker compose --profile container build sandbox-image` 成功，`yuxi-sandbox:0.8.0` 镜像 ID 为 `sha256:9332fd61328bf78a01956e010d858480c2af8ea646eaa312c596d32adb3ee8af`。
- 容器内事实：`pandas 3.1.1`（`Requires: numpy, python-dateutil`）、`pyarrow 26.0.1`、`openpyxl` 可导入。
- 真实链路（默认 agent `default-chatbot`，`scripts/demo/yuxi_client.py` 上传两个附件，模型 `siliconflow-cn:deepseek-ai/DeepSeek-V4.1-Flash`，三次 `execute` 审批均显式批准）：`demo_sales.xlsx` 明细表 4 行、销售额合计 54000，汇总表 3 行、合计 54000；`demo_orders.parquet` 8 行、金额合计 2818.0。Turn 终态 `completed`，输出来自顶层 Run。
- 未验证范围：容器沙盒 Docker 集成测试（`SANDBOX_CONTAINER_TEST=1`）未在本机重跑；改动不触及 runner 代码，只改变镜像内容。
