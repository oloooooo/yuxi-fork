# scripts/demo

面向本地 Yuxi 实例的 6 个演示脚本，只依赖标准库（`scripts/demo/yuxi_client.py` 也在标准库范围内），不改动后端代码，错误只打印一行并返回非 0，不影响当前终端。

## 前置条件

- 服务已启动：`docker compose --profile container up -d`，`curl http://localhost:5050/api/system/ready` 返回 `"status":"ready"`。
- 有一个可用账号。全新实例用 `POST /api/auth/initialize` 建管理员，例如：

  ```bash
  curl -X POST http://localhost:5050/api/auth/initialize \
    -H 'Content-Type: application/json' \
    -d '{"uid":"admin","password":"YuxiDemo12345"}'
  ```

- 运行 Python：任选其一
  - `uv run --no-project python <脚本>`（推荐，无需本机装 Python）
  - 已安装的 `python3` / `python`
- 脚本 04、06 需要**超级管理员**（`role=superadmin`）。

## 快速开始

```bash
cd <仓库根目录>

# 1. 登录，拿到两个环境变量（同时写入 scripts/demo/.env.local，已被 .gitignore 忽略）
eval "$(uv run --no-project python scripts/demo/01_get_token.py --uid admin --password 'YuxiDemo12345')"

# 2. 看当前可用 agent
uv run --no-project python scripts/demo/03_list_agents.py

# 3. 建私有 agent 并绑定 skill 压缩包
uv run --no-project python scripts/demo/02_agent_with_skill.py \
  --skill my-skill.zip --name "私有技能助手" --slug my-skill-agent

# 4. 建共享 skill 并分配给某个 agent
uv run --no-project python scripts/demo/04_share_skill_to_agent.py \
  --skill shared-skill.zip --agent my-skill-agent

# 5. 上传文件到工作区
uv run --no-project python scripts/demo/05_upload_to_workdir.py --file report.pdf

# 6. 建自定义模型供应商
uv run --no-project python scripts/demo/06_create_provider.py \
  --provider-id demo-provider --display-name "演示供应商" \
  --provider-url https://api.siliconflow.cn/v1 --api-key-env SILICONFLOW_API_KEY \
  --model deepseek-ai/DeepSeek-V4.1-Flash
```

## 文件说明

| 文件 | 作用 |
|---|---|
| `yuxi_api.py` | 共用工具：读 `.env.local`、装配 `yuxi_client.Client`、发送 multipart 请求、统一错误输出、Windows 控制台 UTF-8 输出 |
| `yuxi_client.py` | 本地交互客户端（仅标准库），带附件、切模型，直接和后端对话 |
| `01_get_token.py` | 密码登录，输出 `YUXI_BASE_URL` / `YUXI_ACCESS_TOKEN`，并写入 `.env.local` |
| `02_agent_with_skill.py` | 创建私有 agent，并把 zip 直接绑定为该 agent 的专属 skill |
| `03_list_agents.py` | 列出当前用户可见的 agent 及其 slug |
| `04_share_skill_to_agent.py` | 上传 skill → 全局共享 → 分配给指定 agent |
| `05_upload_to_workdir.py` | 上传本地文件到工作区目录 |
| `06_create_provider.py` | 创建自定义模型供应商，可一次启用多个 chat 模型 |

各接口的功能清单见 [API.md](API.md)。

## 各脚本用法

### 01_get_token.py

```bash
eval "$(python3 scripts/demo/01_get_token.py --uid admin --password '***')"
```

| 参数 | 说明 |
|---|---|
| `--uid` | 登录标识：用户 ID 或手机号；缺省时交互输入 |
| `--password` | 密码；缺省时交互输入（getpass） |
| `--base-url` | Yuxi API 地址，默认 `YUXI_BASE_URL` 或 `http://127.0.0.1:5050` |
| `--save` | 写出环境变量的文件，默认 `scripts/demo/.env.local` |

输出到 stdout 的只有两行 `export ...`，可直接 `eval`；登录信息打到 stderr。也支持用 `YUXI_UID` / `YUXI_PASSWORD` 环境变量代替参数。token 约 7 天过期，过期后重跑即可。

### 02_agent_with_skill.py

```bash
python3 scripts/demo/02_agent_with_skill.py --skill my-skill.zip --name "私有技能助手"
```

| 参数 | 说明 |
|---|---|
| `--skill` | 必填；`.zip`（内含唯一 `SKILL.md`，≤10 MiB）。该接口只接受 zip，直传 `.md` 请用 04 |
| `--name` | 必填；agent 显示名 |
| `--slug` | 可选；自定义 slug，默认后端按名称生成 |
| `--backend` | AgentBackend ID，默认 `ChatbotAgent` |
| `--description` | agent 描述 |

调用 `POST /api/agent/with-skill`，新建 agent 默认 `visibility=private`，zip 会作为专属 skill 绑定（数据库里 `skills.bound_agent_id` 指向该 agent）。

`POST /api/agent/with-skill` 会校验扩展名，非 `.zip` 返回 422。

### 03_list_agents.py

```bash
python3 scripts/demo/03_list_agents.py            # slug<TAB>name<TAB>visibility
python3 scripts/demo/03_list_agents.py --slug-only  # 一行一个 slug
```

### 04_share_skill_to_agent.py

```bash
python3 scripts/demo/04_share_skill_to_agent.py --skill shared-skill.zip --agent my-skill-agent
```

三步：`POST /api/skills/import/prepare`（草稿）→ `POST /api/skills/install-drafts/{draft_id}/confirm`（`read_scope=global`）→ `PUT /api/agent/{slug}` 写入 `config_json.context.skills`。

| 参数 | 说明 |
|---|---|
| `--skill` | 必填；同上 |
| `--agent` | 必填；接收 skill 的 agent slug |

注意：agent 原来的 `skills` 若为 `all`，会被收窄成只含该 skill 的列表；若已是列表则追加。需要超级管理员。

### 05_upload_to_workdir.py

```bash
python3 scripts/demo/05_upload_to_workdir.py --file a.csv --file b.csv
python3 scripts/demo/05_upload_to_workdir.py --file a.csv --parent /uploads
```

| 参数 | 说明 |
|---|---|
| `--file` | 必填，可重复；单文件 ≤100 MB，单次 ≤50 个 |
| `--parent` | 目标虚拟路径；缺省取第一个项目的 `workdir_path`，没有项目时用 `/` |

调用 `POST /api/workspace/upload`。父目录必须已存在；沙盒内看到的位置是 `/home/user/workspace/<path>`。

### 06_create_provider.py

```bash
python3 scripts/demo/06_create_provider.py \
  --provider-id demo-provider --display-name "演示供应商" \
  --provider-url https://api.siliconflow.cn/v1 --api-key-env SILICONFLOW_API_KEY \
  --model deepseek-ai/DeepSeek-V4.1-Flash --model zai-org/GLM-5.3
```

| 参数 | 说明 |
|---|---|
| `--provider-id` | 必填；字母/数字/`_`/`-`，长度 2–100，全局唯一 |
| `--provider-url` | 必填；OpenAI 兼容 API 基础 URL |
| `--provider-type` | `openai`（默认）\|`anthropic`\|`gemini`\|`openrouter` |
| `--api-key-env` | 密钥所在环境变量名（推荐），如 `SILICONFLOW_API_KEY` |
| `--api-key` | 直接写入密钥，会明文入库 |
| `--model` | 必填，可重复；只支持 chat 类型 |
| `--display-name` | 展示名称，默认同 provider-id |

`--api-key-env` 与 `--api-key` 必须二选一。创建后脚本打印 `provider_id:model_id`，直接在 `yuxi_client.py` 里选模型：

```bash
SPEC_MODEL=demo-provider:deepseek-ai/DeepSeek-V4.1-Flash TOKEN=$YUXI_ACCESS_TOKEN \
  python3 scripts/demo/yuxi_client.py
```

## 公共约定

- 每个脚本都接受 `--base-url`（默认 `YUXI_BASE_URL`）和 `--token`（默认 `YUXI_ACCESS_TOKEN`），所以 01 之后其余脚本无需再传。
- 凭据缓存文件 `scripts/demo/.env.local` 含 access token，已被 `.gitignore` 忽略；不要提交。
- 失败时输出 `失败：<原因>` 到 stderr 并返回非 0，不会中断当前 shell。
- 在 Windows 上管道给 `yuxi_client.py` 传中文输入时加 `PYTHONUTF8=1`，否则 `input()` 按 cp936 解码会报 surrogates 错误。
