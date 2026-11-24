# 决策：允许连接器创建者维护非敏感连接配置

状态：implemented
类型：feature
Owner：backend/yuxi/api/routers/extensions/mcp.py

## 问题

部门管理员创建连接器后无法修改地址、超时等配置：`PUT /api/system/mcp-servers/{slug}` 只允许超级管理员，创建者只能删除或调整共享范围。任何改动都要找超管，部门自建连接器实际上不可维护。

同时不能简单把编辑权下放：编辑表单需要回填 `headers`，而共享请求头包含凭据明文，非超管本来就拿不到脱敏后的 `headers`。

## 决策

- 创建者（`created_by` 等于当前用户且对该连接器有管理权）可以修改地址、描述、HTTP 超时与 SSE 读取超时。
- 传输方式、接入方式、请求头、OAuth 配置仍只允许超级管理员修改；非超管提交这些字段返回 403。
- 认证配置只支持整体重置：非超管带 `reset_auth=true` 时才能提交认证字段，否则一律忽略并拒绝，避免回填泄露。
- 序列化对创建者返回 `url`（编辑需要），`headers` 仍只返回给超级管理员；新增 `can_edit_config` 标识控制前端入口。

## 替代方案

- 完全下放编辑权：会让非超管读到共享凭据明文，否决。
- 只允许超管编辑：保留现状，部门自建连接器不可维护，否决。
- 用「新建替代再删除」绕开编辑：重建会丢失共享范围、依赖关系和已配置的个人凭证，否决。

## 后果

- 创建者可自助改地址与超时；凭据轮换需走「重置认证」并完整重填，不能查看旧值。
- 后端 `update_mcp_server` 增加 `auth_untouched`，只改地址时不再重跑 `configure_mcp_auth`，避免清掉 OAuth 配置。
- 依赖该地址的 Skill 无需重新安装；下一轮对话按新地址连接。

## 验证

- 集成测试 `backend/test/integration/api/test_department_admin_scope.py::test_creator_admin_edits_non_sensitive_fields_and_resets_auth_only`：创建者改地址/超时/描述 200，共享请求头保持不变；未带 `reset_auth` 提交 `headers` 或 `transport` 返回 403；带 `reset_auth` 切到无需认证后 `headers` 清空；同部门其他管理员返回 403 且 `can_edit_config=false`。
- 真实页面：创建者详情页出现「编辑」，表单中地址可改、传输方式禁用、认证区显示「共享请求头（已配置，不可查看）」与「重置认证」；保存后地址更新、请求头未变；重置认证后 `auth_mode=none`、`headers={}`。
- 全量 `pytest test/unit -m "not slow"` 与工程契约检查通过。
