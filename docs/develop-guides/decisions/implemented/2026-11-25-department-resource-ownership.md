# 决策：部门资源用归属部门延续管理权

状态：implemented
类型：feature
Owner：backend/yuxi/modules/identity/permissions/resource_permission.py

## 问题

部门管理员把本部门共享的 Skill、Agent 或连接器的读取范围改成「指定人」后，共享范围不再包含部门，权限解析随即判定其无权管理——刚分配出去的资源当场失去维护入口，也无法改回部门共享。

## 决策

在 `share_config` 中记录 `owner_department_id`：管理员创建资源时写入其部门，之后调整读取范围不丢失。权限解析时，归属部门的管理员对本资源保留管理权，与「部门共享范围」同等对待。

## 实现方案

- `normalize_permission_config` 透传并规范化 `owner_department_id`；`stamp_owner_department` 在创建与更新共享配置时写入（已有值优先，管理员缺省取自身部门）。
- `resolve_resource_permission` 在 `_administers_department(read_scope)` 之外增加归属部门判定，作用于 Skill、Agent、连接器与知识库共用的解析路径。
- 写入点：`create_agent_definition`/`update_agent_definition`、Skill 安装确认与 `update_skill_share_config`、`create_mcp_server`/`update_mcp_share_config`。

## 替代方案

- 把 `manage_scope` 设为部门：被「管理范围必须包含在读取范围内」的既有约束拒绝。
- 新增数据库列：三张资源表都要迁移，收益与 JSON 字段相同。

## 后果

- 部门管理员可以把资源分配给任意指定用户，并继续维护或收回。
- 超级管理员创建的资源默认没有归属部门，仍按共享范围判定；部门管理员只有在把资源纳入本部门共享后才会写入归属部门。
- 归属部门只影响管理权，不影响读取范围。

## 验证

- `backend/test/unit/permissions/test_resource_permission.py::test_department_admin_keeps_manage_after_switching_to_user_scope`：归属部门管理员在指定人范围下仍为 MANAGE，其他部门管理员为 NONE。
- `backend/test/integration/api/test_department_admin_scope.py::test_department_admin_can_assign_department_connector_to_specific_user`：部门管理员把本部门连接器分配给指定用户后 `can_manage` 仍为真、目标用户可见、可改回部门范围。
- 真实页面（Playwright）：部门管理员在连接器详情把共享范围切到「指定人」，保存后页面保留编辑与共享入口。
