# 决策：Skill 缺少 MCP 依赖时提供连接器补建入口

状态：implemented
类型：feature
Owner：backend/yuxi/modules/extensions/skills/access.py

## 问题

安装声明了 `mcp_dependencies` 的 Skill 时，未注册的连接器只在安装结果里留下一条 `存在无效 MCP 依赖: <slug>` 的文本。管理员必须自己记住标识，切到扩展 → 连接器，凭记忆手填名称与标识再回来重装。个人 Skill 安装路径则把依赖直接置空，带 MCP 的 Skill 会静默装上一个永远缺工具的版本。

## 决策

- 依赖校验在缺失时抛 `MissingMcpDependencies`，安装结果带上结构化 `missing_mcps`，安装页据此展示「缺少连接器」并提供「创建连接器」入口。
- 补建复用现有「新增连接器」弹窗，只预填名称与标识（都取缺失的 slug），地址与接入方式仍由管理员填写：MCP 协议要求合法 http(s) 地址，占位连接器会让依赖校验通过但运行期工具不可用。
- 个人 Skill 不支持 MCP 依赖，确认安装时显式失败（`个人 Skill 不支持 MCP 依赖，请由管理员安装为共享 Skill`），不再静默丢弃。
- 只校验 Skill 自身的 `mcp_dependencies`；组合型 Skill 通过 `skill_dependencies` 引用已注册的 Skill，不递归校验子 Skill 的依赖，保持放行。

## 替代方案

- 允许空地址的占位连接器：需要新增状态与迁移，且依赖校验放宽后 Skill 会在工具不可用的情况下「安装成功」。
- 前端解析错误文本提取 slug：依赖文案，后端一改就断。
- 在安装流程内嵌补建表单：与连接器管理页表单重复维护。

## 后果

- 管理员补建后回到确认页需要再点一次安装；不回显重试，避免表单状态与共享范围在后台静默提交。
- 个人 Skill 带 MCP 依赖从「静默降级」变为「明确拒绝」，属于行为变更。

## 验证

- `backend/test/unit/services/skills/test_skill_draft.py::test_personal_install_rejects_mcp_dependencies` 覆盖个人路径拒绝且不落盘。
- 真实 HTTP：`POST /api/skills/install-drafts/{id}/confirm` 返回 `missing_mcps: ["demo-missing-mcp"]`。
- Playwright：失败页出现「缺少连接器：demo-missing-mcp」与「创建连接器」，弹窗名称/标识预填为该 slug、地址为空；补建后重装成功；个人路径显示拒绝文案（截图 `.yuxi/playwright/mcpfill-01..08`）。
- 未验证：一次缺多个连接器时的连续弹窗、已被 Agent 引用的连接器删除提示。
