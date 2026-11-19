# Skill / Agent / 对话 API 功能说明

只描述每个接口能做什么、需要什么输入、返回什么结果，不涉及内部实现。所有路径都以 `/api` 为前缀，鉴权统一用 `Authorization: Bearer <access_token>`（token 由 `01_get_token.py` 或 `POST /api/auth/token` 获得）。

## 一、Agent（智能体）

Agent 是"人格 + 模型 + 上下文配置 + 可用 skill"的持久定义，通过 slug 引用。`visibility` 有两种：`private`（仅自己）和 `shared`（可见范围内的其他人也能用）。

| 方法 | 路径 | 功能 |
|---|---|---|
| GET | `/agent/backends` | 列出可用的 Agent 类型（决定 `backend_id` 能填什么，如 `ChatbotAgent`） |
| GET | `/agent` | 列出当前用户可见的 agent（自己的 + 共享的），含 slug、名称、可见性、配置 |
| GET | `/agent/{slug}` | 读取单个 agent 的完整定义，含可配置项 |
| GET | `/agent/default` | 读取平台默认智能体（新会话未指定 agent 时使用） |
| POST | `/agent` | 创建 agent。必填 `name`；可选 `slug`（不填由后端生成）、`description`、`icon`、`visibility`（默认 private）、`config_json`、`share_config` |
| POST | `/agent/with-skill` | 创建 agent 并同时上传它的**专属 skill**：multipart，`agent` 字段为上面的 JSON 字符串，`file` 字段为 `.zip`（内含唯一 `SKILL.md`，≤10 MiB） |
| PUT | `/agent/{slug}` | 修改 agent 的名称、描述、图标、`config_json`、共享范围；`visibility` 只能改成 `shared`。`config_json` 是合并写入，`context` 会与旧值浅合并 |
| DELETE | `/agent/{slug}` | 删除自己创建的非内置 agent；内置返回 409，别人的返回 403 |
| POST | `/agent/{slug}/set_default` | 超级管理员设置平台默认智能体 |
| GET | `/agent/{slug}/self-skill` | 读取该 agent 的专属 skill（内容与版本） |
| POST | `/agent/{slug}/self-skill` | 幂等创建空的操作指南型专属 skill |
| POST | `/agent/{slug}/self-skill/upload` | 给尚无专属 skill 的 agent 导入 `.zip`；已有绑定返回 409 |

与对话直接相关的两个配置项：

- `config_json.context.skills`：该 agent 可用的 skill，值为 `"all"`（默认，全部可见 skill）或 slug 列表。
- `config_json.context.preload_skills`：预加载的 skill slug 列表。
- `share_config`：共享范围，形状同下文的 skill 共享配置（`read_scope.access_level` 等）。

## 二、Skill（技能包）

Skill 是给 agent 使用的说明文档包（`.zip` 或单个 `SKILL.md`，解压 ≤50 MiB / 2000 项），有"专属"和"共享"两种归属：专属 skill 绑定某一个 agent，共享 skill 落在 skill 库里再分配给 agent。

### 上传与安装

| 方法 | 路径 | 功能 |
|---|---|---|
| POST | `/skills/import/prepare` | 上传 skill 包生成**安装草稿**：multipart `file`；返回 `draft_id`、解析出的 `items`（含 slug）、`failures`、当前用户可选 `allowed_access_levels`、默认 `default_share_config`。此时还未入库 |
| POST | `/skills/install-drafts/{draft_id}/confirm` | 管理员确认草稿为**共享 skill**：JSON `{"slugs":[...],"share_config":{...}}`，可只装其中几个 |
| POST | `/skills/personal/install-drafts/{draft_id}/confirm` | 普通用户确认草稿为**个人 skill**：JSON `{"slugs":[...]}`，仅自己可用 |
| DELETE | `/skills/install-drafts/{draft_id}` | 丢弃草稿，不上传任何内容 |

### 查询

| 方法 | 路径 | 功能 |
|---|---|---|
| GET | `/skills` | 列出当前用户**可管理**的共享 skill |
| GET | `/skills/accessible` | 列出当前用户**可使用**的 skill（含被共享和个人的），用于给 agent 选 skill |
| GET | `/system/skills` | 管理端列出共享 skill 库 |
| GET | `/system/skills/{slug}` | 读取单个 skill 的元数据 |
| GET | `/system/skills/{slug}/tree` | 读取 skill 的文件树 |
| GET | `/system/skills/{slug}/file` | 读取单个文件内容 |
| GET | `/system/skills/{slug}/content` | 读取整包文本内容 |
| GET | `/system/skills/{slug}/versions` | 查看版本历史 |

### 修改与共享

| 方法 | 路径 | 功能 |
|---|---|---|
| PUT | `/system/skills/{slug}/share-config` | 修改共享范围，body `{"share_config":{...}}`，权限不足返回 403 |
| PUT | `/system/skills/{slug}/enabled` | 启用/停用 skill |
| PUT | `/system/skills/{slug}/file`、POST 同路径 | 新增或覆盖文件 |
| PUT | `/system/skills/{slug}/content` | 整体覆盖文本内容 |
| DELETE | `/system/skills/{slug}/file` | 删除单个文件 |
| DELETE | `/system/skills/{slug}` | 删除整包 skill |
| POST | `/system/skills/delete-batch` | 批量删除，body `{"slugs":[...]}` |
| POST | `/system/skills/{slug}/versions/{version}/restore` | 回滚到历史版本 |
| GET | `/system/skills/{slug}/export` | 导出为 zip |

共享范围的 `share_config` 结构：

```json
{"version": 2,
 "read_scope": {"access_level": "global", "department_ids": [], "user_uids": []},
 "manage_scope": null}
```

`access_level` 取值 `global`（所有人）、`department`（指定部门）、`user`（指定用户，上传默认值）。`manage_scope` 为 `null` 表示沿用默认管理权限。

## 三、对话（Session / Turn / Event）

对话走 public v1 接口（`/api/v1/agents`），按"会话 → 输入事件 → 轮次"组织。客户端可持用户 token，也可用平台 API Key（由后端按 key 的授权范围决定可见的 agent 与会话）。

### 会话

| 方法 | 路径 | 功能 |
|---|---|---|
| GET | `/v1/agents` | 列出当前可对话的 agent（目录） |
| GET | `/v1/agents/{slug}` | 读取单个 agent 的对话能力信息 |
| POST | `/v1/agents/sessions` | 创建会话，**必须带 `Idempotency-Key` 请求头**。body 关键字段：`agent_id`（slug）、`title`、`project_id`、`yuxi.attachment_file_ids`、`yuxi.mcps`、`agent.model`（本次会话的模型覆盖）、`input`（首批消息，可空）、`stream`（true 时直接返回 SSE，必须同时给 `input`）。返回会话对象，含 `id`、`agent.{id,model}`、`status`、`receipt` |
| GET | `/v1/agents/sessions` | 分页列出会话：`agent_id`、`after`、`order`、`archived`、`is_pinned`、`limit` |
| GET | `/v1/agents/sessions/search` | 按关键词搜索会话标题与历史消息 |
| GET | `/v1/agents/sessions/{id}` | 读取会话状态（`idle`/`in_progress`/`requires_action`/`completed`/`failed`/`cancelled`）、当前轮次、队列长度 |
| POST | `/v1/agents/sessions/{id}` | 修改标题、置顶状态、默认审批模式 |
| POST | `/v1/agents/sessions/{id}/archive` | 归档会话 |
| POST | `/v1/agents/sessions/{id}/viewed` | 标记已读 |
| GET | `/v1/agents/sessions/{id}/queue` | 查看待处理输入队列 |

### 发消息与订阅

| 方法 | 路径 | 功能 |
|---|---|---|
| POST | `/v1/agents/sessions/{id}/events` | 提交一个事件，**必须带 `Idempotency-Key`**，返回 202 与回执（`input_id`/`turn_id`/`run_id`）。202 只表示"已接收"，最终结果需读取轮次 |
| GET | `/v1/agents/sessions/{id}/events` | 订阅会话的长期 SSE 事件流；断线可带 `Last-Event-ID` 续订，客户端在拿到终态后自行关闭 |

事件的 `type` 与用途：

| type | 功能 |
|---|---|
| `agent.session.input.message` | 发送用户消息，`input` 为消息数组（纯文本/图片 part），`yuxi.mode` 选 `steer`（运行中插入）或 `follow_up`（排队等本轮结束，默认） |
| `agent.session.input.cancel` | 取消当前轮次（可指定 `turn_id`、`expected_run_id`） |
| `yuxi.session.input.continue` | 恢复被暂停的待办队列 |
| `yuxi.session.input.promote` | 把某个排队输入提到队首 |
| `yuxi.session.input.cancel_input` | 取消某个排队输入 |
| `yuxi.session.input.resume` | 回答提问或审批：`turn_id` + `waitpoint_id` + `response`（`answer` 或 `approval`） |
| `yuxi.session.tree.stop` / `continue` | 停止或继续多智能体协作 |

消息事件支持的 `yuxi` 选项：`attachment_file_ids`（先上传的草稿文件）、`mcps`（本次启用的 MCP）、`model`（本次 follow_up 的模型覆盖，`steer` 不允许）、`tool_approval_mode`。

### 读结果

| 方法 | 路径 | 功能 |
|---|---|---|
| GET | `/v1/agents/sessions/{id}/turns` | 分页读取轮次列表 |
| GET | `/v1/agents/sessions/{id}/turns/{turn_id}` | 读取单轮的终态与最终输出 |
| GET | `/v1/agents/sessions/{id}/turns/{turn_id}/items` | 分页读取该轮公开内容（消息、工具调用、产物等） |
| GET | `/v1/agents/sessions/{id}/inputs/{input_id}` | 查询某次输入被哪个轮次消费 |
| GET | `/v1/agents/sessions/{id}/receipt?idempotency_key=` | 用幂等键找回接受回执 |
| GET | `/v1/agents/sessions/{id}/runs/{run_id}` | 读取单次运行的详情 |
| GET | `/v1/agents/sessions/{id}/audits` | 读取会话审计记录 |

### 附件与产物

| 方法 | 路径 | 功能 |
|---|---|---|
| POST | `/v1/agents/files` | 上传未提交的文件草稿（≤5 MiB），返回 `file_id`，供 `attachment_file_ids` 引用 |
| GET / DELETE | `/v1/agents/files/{file_id}` | 读取元数据 / 丢弃草稿 |
| GET | `/v1/agents/sessions/{id}/attachments` | 列出会话附件 |
| DELETE | `/v1/agents/sessions/{id}/attachments/{file_id}` | 从会话移除附件 |
| GET | `/v1/agents/sessions/{id}/artifacts/{path}` | 下载会话产物（沙盒生成的文件） |
| POST | `/v1/agents/sessions/{id}/artifacts/save` | 把产物保存到工作区 |
| GET | `/v1/agents/sessions/{id}/state` | 读取会话运行态快照 |
| GET | `/v1/agents/sessions/{id}/cooperation` | 读取多智能体协作状态 |
| POST | `/v1/agents/sessions/{id}/compress` | 压缩历史上下文（减少 token 占用） |

## 四、错误与语义

| 状态码 | 含义 |
|---|---|
| 401 | token 无效或过期，重新登录 |
| 403 | 有身份但无权限（非管理员、非创建者、skill 不可管理） |
| 404 | 资源不存在或对当前用户不可见 |
| 409 | 状态冲突（重复绑定 skill、等待回答/审批时不能发普通消息、幂等键复用了不同意图） |
| 413 | 上传超过体积限制（zip 10 MiB、附件 5 MiB、工作区单文件 100 MB） |
| 422 | 参数不合法（缺字段、agent 不可访问、事件类型与内容不匹配） |

- 所有写事件接口都用 `Idempotency-Key` 保证重试安全：同键同意图返回同一回执，同键不同意图返回 409。
- 会话的 `status` 与轮次终态是判断"是否回答完"的依据；HTTP 202/200 不代表模型已答完。
- 上传到工作区不是对话附件：工作区用于沙盒文件，附件用于随消息发送；两者不能混用。
