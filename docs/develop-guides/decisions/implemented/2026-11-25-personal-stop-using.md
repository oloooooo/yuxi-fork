# 决策：允许用户个人停止使用已授权的资源

状态：implemented
类型：feature
Owner：backend/yuxi/modules/identity/subscriptions/service.py

## 问题

普通用户拿到 Agent、Skill、连接器的使用权后，只能一直保留。他们没有删除权限，也没有共享范围的编辑权，因此无法把不用的资源从自己的工作台里移走；资源市场只列出「还不可用」的资源，已可用的资源没有操作入口。

## 决策

在 `resource_subscriptions` 里复用 `status="disabled"` 记录「本人主动停止使用」。停止与恢复都只作用于操作者本人：共享范围不变，其他用户的可见性不变，也不需要重新审批。

`status="disabled"` 只影响读取路径：Agent 列表与详情、Skill 列表与授权投影、连接器的列表与运行时装配都会把该用户的停用项过滤掉；对资源拥有管理权的用户不受影响，仍能在管理页看到并恢复。

## 实现方案

- `backend/yuxi/modules/identity/subscriptions/service.py`：`stop_using`、`resume_using`、`disabled_slugs`；`list_catalog` 对停用项返回 `visible=false` 与 `status=disabled`；再次提交申请时，已停用的记录直接恢复为 `approved`。
- `POST /api/subscriptions/stop`、`POST /api/subscriptions/resume` 两个路由，参数与申请相同。
- 过滤点：`AgentRepository`、`SkillRepository` 的 `_disabled_slugs`，连接器的 `get_enabled_mcp_servers`、`available_mcp_servers` 与列表路由。
- 前端资源市场对已可用资源显示「停止使用」，对停用项显示「恢复使用」；「我的申请」同样可以恢复。

## 替代方案

- 让用户删除共享资源：等于把管理权交出去，会影响其他用户。
- 新建一张「个人停用表」：`resource_subscriptions` 已经按用户与资源唯一，复用状态字段就够了。
- 停用即撤销订阅：恢复要重新审批，日常使用不划算。

## 后果

- 停用状态属于个人偏好，不会出现在审批队列里，也不通知管理员。
- 停用项仍保留在数据库中；管理员看到的资源状态与访问控制不受影响。
- 停用只是过滤读取路径，不改变 `enabled`、`share_config` 或任何其他用户的授权。

## 验证

- `backend/test/integration/api/test_subscriptions_api.py::test_user_stops_and_resumes_shared_resource` 覆盖「可见 → 停止后列表消失、目录 `status=disabled` → 恢复后重新可见」。
- 真实页面（Playwright）：`declauding` Skill 在资源市场点「停止使用」后 `/api/skills` 不再返回该 slug，卡片显示「已停止使用」；点「恢复使用」后重新返回并显示「已可用」。
- 全量 `pytest test/unit -m "not slow"`、工程契约检查、前端 `lint:check` 与 `build` 均通过。
