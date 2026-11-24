# 决策：把管理员收敛为部门管理员

状态：implemented
类型：feature
Owner：backend/yuxi/modules/identity/permissions/resource_permission.py

## 问题

`admin` 同时被当作"部门账号管理员"和"全平台资源治理者"：MCP 权限解析对 `admin` 直接返回管理权，而 Agent、Skill、知识库按共享范围与创建者判定；连接器又不允许管理员设置全平台共享。结果是管理员能删除任意部门的连接器、把全平台连接器收窄到本部门，却读不到连接器地址与凭据、也无法编辑或测试。角色含义前后不一致，每次新增功能都要重新决定管理员是否算数。

## 决策

- 管理员是部门管理员：对本部门共享范围（读取范围为 `department` 且包含其部门）内的资源拥有管理权。
- 全平台（`global`）共享资源对管理员只读，管理动作仍属超级管理员。
- 管理员新建连接器、Skill、Agent 默认本部门可见；可设置的范围只有部门和指定用户，不能设为全平台。
- 连接器的编辑、启停、连接测试与凭据读取仍只属超级管理员。
- 资源订阅审批仍只属超级管理员。

## 实现方案

- `resolve_resource_permission` 新增部门判定：管理员命中部门范围时获得管理权；`resolve_mcp_permission` 删除 `admin → MANAGE` 的角色短路，与 Agent、Skill、知识库同构。
- `default_mcp_share_config`、`default_skill_share_config`、`default_agent_share_config` 按角色给出默认范围（超管全平台、管理员本部门、其他本人）。
- `get_allowed_skill_access_levels`、`allowed_agent_access_levels` 对管理员去掉全平台；`validate_shared_grants` 接受可设定级别上限，智能体创建与更新时按角色校验。
- 前端 Agent 编辑页在遇到超出当前角色上限的既有范围（如全平台）时显示只读提示，避免被表单静默改写。
- 知识库沿用原有写入路径与创建者管理权：知识库默认全平台共享，创建者保留管理权。

## 替代方案

- 保留管理员全平台治理：与共享范围决定管理对象的既有承诺冲突，`manage_scope` 失去意义。
- 新增"部门管理员"第四角色：需要数据库约束迁移并改动所有角色判定、前端二元谓词与测试夹具，成本远高于收益。
- 收回管理员建设能力（只保留账号管理）：会失去部门自治，所有共享资源都要超管维护。

## 后果

- 管理员不再能管理其他部门或全平台的连接器与共享资源；依赖该能力的操作需由超管完成。
- 管理员新建资源默认对全部门可见（此前连接器、Skill 仅本人可见），范围更宽但可随时收窄到指定用户。
- 管理员编辑既有的全平台资源时，共享范围表单变为只读提示，不会静默改写。
- 个人凭证、连接器凭据与地址仍不对管理员暴露。

## 验证

- `backend/test/unit/permissions/test_mcp_share_scope.py`：管理员对全平台连接器只读、对本部门连接器可管理、其他部门无权限、默认范围为本部门。
- `backend/test/integration/api/test_department_admin_scope.py`：真实 HTTP 下部门管理员只能删除本部门连接器、不能删除全平台或其他部门连接器、设全平台返回 400、新建连接器默认本部门。
- 全量单元测试与前端 lint/build 通过；Playwright 在真实页面验证管理员视角的列表、详情与共享范围表单。
