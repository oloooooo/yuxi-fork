# 决策：连接器共享范围

状态：implemented
类型：feature
Owner：backend/yuxi/modules/extensions/mcp/service.py

## 问题

连接器（MCP server）此前只有“能用/不能用”两个开关：任何管理员建好的连接器，所有登录用户都能在自己的 Agent 里绑定，绑定后调用时使用的是管理员写进共享请求头的服务身份。连接器目录因此是隐式全平台公开：无法只给某个部门或某些人使用，管理员也不能把自己维护的连接器限制在部门内；普通用户在页面上也看不到自己实际能不能用某个连接器。Skill 已经有全局/部门/用户三档共享范围，连接器缺少同等能力，导致“管理员维护、按范围分发”的治理需求只能靠不建连接器来回避。

## 决策

给 `mcp_servers` 增加与 Skill 同构的 `share_config`（version 2 的 read_scope/manage_scope），用一套权限解析决定“可见（READ）/可管理（MANAGE）”，并在所有会产出工具的运行时入口按当前用户过滤。创建与删除连接器从“仅超级管理员”放宽到管理员，共享范围可由管理员修改；连接参数编辑、启用/停用与连接测试仍限超级管理员，因为编辑表单需要回读共享凭据。管理员不能用全平台共享，只能指定部门或用户，超级管理员可选全平台。普通用户仍不能创建或修改连接器。

### 实现方案

1. 数据：`MCPServer` 新增可空 `share_config` JSON 列；业务 Schema 升到 v3，新增 `backend/yuxi/migrations/mcp_share_config.py` 做 `ADD COLUMN IF NOT EXISTS` 并把存量行回填为全平台可见，`app-init` 负责执行；新库直接按 v3 建表。
2. 权限：`backend/yuxi/modules/identity/permissions/resource_permission.py` 复用 `resolve_resource_permission` 与 `ResourcePermissionPolicy`，新增 `resolve_mcp_permission`——超级管理员恒为 MANAGE；管理员对全部连接器为 MANAGE（连接器是基础设施资源，没有“所有者之外不可管理”的语义）；其余按 read_scope/manage_scope 匹配，已删除用户为 NONE。
3. 范围校验：`service.normalize_mcp_share_config` 复用 `normalize_permission_config(strict=True)`，并按角色限制可选范围（`allowed_mcp_access_levels`）：超管全局/部门/用户，管理员部门/用户，其他人仅本人。管理员提交全平台范围在 API 层返回 400。
4. 服务：`create_mcp_server` 写入调用者的默认范围（超管全局、管理员仅本人）；`get_enabled_mcp_servers` 增加可选 `user` 并在传入时按可见性过滤，Agent 上下文选项与运行时装配都传当前用户；`available_mcp_servers` 在计算个人凭证状态前再按可见性过滤一次，范围被撤销的服务器在下一轮静默消失；新增 `update_mcp_share_config`。
5. 路由：创建/删除/共享范围要求管理员并校验 MANAGE；详情与工具列表放宽到登录用户 + READ，越权 403；启用/停用、编辑、测试仍限超级管理员；`serialize_mcp_server` 按查看者计算 `can_manage` 与 `allowed_access_levels`，非超管隐藏 `url`/`headers`（共享凭据不可回读）。
6. 前端：连接器卡片对管理员显示“新增”；详情页对可管理者显示“共享范围”（复用 Agent 的 `ShareConfigForm`），编辑/启停仅超管可见，删除按 `can_manage` 显示。

## 替代方案

- 只加数据列、不加运行时过滤：页面看起来收窄了，但 Agent 仍能绑定并调用被隐藏的连接器，授权只在 UI 执行，违背“后端最终执行”。
- 让普通用户自建连接器并共享：用户最终裁定不做，连接器属于部署方维护的基础设施，个人自建会把共享凭据的治理责任扩散到所有人。
- 复用 Skill 的 `created_by == uid` 才能管理：管理员无法接管离职或历史创建者维护的连接器，运维上不可接受；改为管理员对整个目录有管理权。
- 多部门/部门树匹配：当前用户模型只有单值 `department_id`，做多值匹配需要先改身份模型，范围外。

## 后果

- 连接器可见性成为持久事实：撤销共享后，绑定它的 Agent 在下一轮运行中不再获得对应工具，且不会显式报错；排查时需要看连接器详情里的共享范围。
- 管理员能看到并管理超级管理员建的连接器，也可以删除；这是有意放宽，审计依赖 `updated_by`/`created_by` 与操作日志。
- 除超级管理员外，连接器地址与共享请求头不再出现在任何接口响应里；管理员编辑表单仍需要超管权限，不存在“管理员看得到但改不了”的中间态。
- 存量部署必须显式跑 v3 迁移，否则运行进程因 Schema 版本不匹配拒绝启动。
- 个人凭证语义不变：共享范围只决定可见性，不改变 share headers 由所有人共用这一事实。

## 验证

- 单元：`backend/test/unit/permissions/test_mcp_share_scope.py` 覆盖超管/管理员/普通用户/已删除用户的权限解析、部门单值匹配、管理员不能设全平台、默认范围、运行时按用户过滤、序列化对管理员隐藏地址与请求头。
- 容器内单测：`test/unit` 全量 3777 passed / 148 skipped；其中 `test/unit/permissions/test_mcp_share_scope.py` 验证权限解析、部门单值匹配、管理员不能设全平台、默认范围、运行时按用户过滤与序列化脱敏。
- HTTP 集成：`test/integration/api/test_mcp_router.py` 23 passed；`test/integration/services/test_app_initialization.py` 与 `test_schema_migration_version.py` 38 passed；另用真实 HTTP 脚本验证 15 项——超管可设 global、管理员设 global 返回 400、管理员设 user 范围后可读、范围外用户绑定 Agent 返回 422 `无权新增智能体资源 mcps: ...`、`version:1` 返回 400。
- 迁移：本地库 business v2 → v3 后 4 个存量连接器全部回填全平台范围，重复执行幂等。
- 前端：`pnpm run lint:check`、`pnpm run build` 通过；`pnpm run test:unit` 唯一失败是 `frontend/src/pages/WorkspaceView.vue` 源码正则断言在 CRLF 检出下不匹配的既有行尾问题。Playwright 真实页面验证：管理员保存「指定人 + 单个用户」后回显与 API 一致、管理员详情页无编辑/启停但有删除与共享范围、普通用户不可见也未越权、超管全平台回显正确。
- 未验证范围：`manage_scope`（管理权限）的页面保存与生效；部门共享分支未做页面验证；共享范围撤销后 Agent 运行时工具消失仅有单测与后端过滤覆盖，未跑一次完整对话；`docker-compose` 的 frontend dev target 缺 CMD 的既有缺陷未修。
