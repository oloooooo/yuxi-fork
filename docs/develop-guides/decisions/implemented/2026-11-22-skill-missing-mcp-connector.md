# 决策：Skill 缺少 MCP 依赖时提供连接器补建入口

状态：implemented
类型：feature
Owner：backend/yuxi/modules/extensions/skills/access.py

## 问题

安装声明了 `mcp_dependencies` 的 Skill 时，未注册的连接器只在安装结果里留下一条 `存在无效 MCP 依赖: <slug>` 的文本。管理员必须自己记住标识，切到扩展 → 连接器，凭记忆手填名称与标识再回来重装。个人 Skill 安装路径则把依赖直接置空，带 MCP 的 Skill 会静默装上一个永远缺工具的版本。

## 决策

- 依赖校验在缺失时抛 `MissingMcpDependencies`，安装结果带上结构化 `missing_mcps`，安装页据此展示「缺少连接器」并提供「创建连接器」入口。
- 安装失败后自动弹出多步骤向导 `McpDependencyWizard`：一页一个缺失连接器，标识锁定为 Skill 声明的 slug，可填名称、传输方式（Streamable HTTP/SSE）、服务地址、访问令牌与描述，支持「上一项」「下一项」与汇总页。
- 补建不单独分配共享范围：创建时沿用 Skill 当前的 `read_scope`；超出创建者权限时降级为「部门」或「仅本人」，之后由 Skill 共享范围变更统一同步。全部建完自动重试失败项。
- 补建前先查已存在连接器：slug 已存在则跳过该步并提示「连接器已存在：…，直接重新安装」，避免同名冲突撞上后端吞掉原因后的通用 400。
- 引导弹窗创建成功后自动重试失败项，回到确认安装页再点一次「确认安装」；不静默跳过失败项，也不自动装未确认的 Skill。
- 补建时把 Skill 当前选的 `read_scope` 作为新连接器的 `share_config`（`POST /api/system/mcp-servers` 新增 `share_config` 字段）：否则超管补建会落成全局可见，没拿到该 Skill 的用户仍能在 `@` 菜单里看到并使用连接器（真实反馈）。管理员试图通过该字段建全局仍被权限校验拦下（400）。
- 个人 Skill 不支持 MCP 依赖，确认安装时显式失败（`个人 Skill 不支持 MCP 依赖，请由管理员安装为共享 Skill`），不再静默丢弃。
- 只校验 Skill 自身的 `mcp_dependencies`；组合型 Skill 通过 `skill_dependencies` 引用已注册的 Skill，不递归校验子 Skill 的依赖，保持放行。

## 替代方案

- 允许空地址的占位连接器：需要新增状态与迁移，且依赖校验放宽后 Skill 会在工具不可用的情况下「安装成功」。
- 前端解析错误文本提取 slug：依赖文案，后端一改就断。
- 在安装流程内嵌只含三个字段的简化表单：与连接器管理页重复维护，且管理员无法一次设好传输方式/认证方式/共享范围，补建完还要回管理页返工（第一版的真实问题）。

## 后果

- 管理员补建后回到确认页需要再点一次安装；不回显重试，避免表单状态与共享范围在后台静默提交。
- 个人 Skill 带 MCP 依赖从「静默降级」变为「明确拒绝」，属于行为变更。

## 验证

- `backend/test/unit/services/skills/test_skill_draft.py::test_personal_install_rejects_mcp_dependencies` 覆盖个人路径拒绝且不落盘。
- 真实 HTTP：`POST /api/skills/install-drafts/{id}/confirm` 返回 `missing_mcps: ["demo-missing-mcp"]`。
- Playwright：失败页出现「缺少连接器：demo-missing-mcp」与「创建连接器」；引导弹窗显示「Skill「垂类搜索」依赖连接器 bailian-vertical-search」，名称预填、服务地址/访问令牌为空；空地址提交被拦；填地址+令牌创建后自动回到确认安装，重装成功（截图 `.yuxi/playwright/mcpfill-01..08`、`guide-00..02`）。
- API 回读补建结果：`url`/`auth_mode=headers`/`headers.Authorization=Bearer …`/`share_config.read_scope.access_level` 与 Skill 当前选择一致（部门范围时落 `department_ids`）。
- 可见性回归：部门 1 的 Skill 补建出的连接器用部门 2 普通账号 `GET /api/system/mcp-servers` 看不到，部门 1 账号能看到；`admin` 建全局范围返回 400。
- 补充说明：标识（slug）唯一，重复创建会被拒；显示名无唯一约束。补建时 slug 只读，名称默认等于 slug，因此正常流程不会撞名。
- Playwright：一次缺两个连接器时的连续补建（`guide-mcp-alpha` → `guide-mcp-beta`）通过，两次创建各自保存独立的 `Authorization` 头，重装成功；结束后删除测试 Skill 与连接器，无残留。
- 未验证：已被 Agent 引用的连接器删除提示（当前删除连接器不检查任何引用，本轮未改动该行为）。
