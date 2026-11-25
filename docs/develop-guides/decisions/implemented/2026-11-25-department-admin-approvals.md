# 决策：订阅按资源范围审批，知识库下放个人

状态：implemented
类型：feature
Owner：backend/yuxi/modules/identity/subscriptions/service.py

## 问题

订阅申请只有超级管理员能审批，部门管理员对「本部门已被共享的资源」没有审批权；知识库创建又只开放给管理员，普通用户无法拥有个人知识库。

## 决策

订阅审批按资源当前共享范围路由：部门范围的资源由包含该部门的部门管理员审批，全平台与指定人范围仍归超级管理员。知识库创建权限作为一次性授权（`resource_type=knowledge_create`、`resource_slug=*`）走同一张订阅表，只由超级管理员审批；获批用户创建的知识库固定为本人可读。普通用户保留 `department_id`，但可见性不按部门隔离，只通过共享范围与订阅获得资源。

## 实现方案

- `backend/yuxi/modules/identity/subscriptions/service.py`：`_approver_can_handle` 判断部门管理员是否覆盖资源范围；`list_pending`/`pending_count` 按处理人过滤；`decide` 对应 403「只能审批本部门范围的申请」；`submit_request` 与 `list_catalog` 支持 `knowledge_create` 伪条目；`has_knowledge_create_grant` 供建库校验。
- `backend/yuxi/api/routers/identity/subscriptions.py`：待审批、数量、批准、驳回四个接口从超管依赖改为管理员依赖，并把当前用户传入服务层。
- `backend/yuxi/api/routers/knowledge/management.py`：创建知识库改为登录用户可用，未获批者返回 403「需要知识库创建权限，请先在扩展页申请」；普通用户强制 `read_scope=user(uid)`。
- `backend/yuxi/api/routers/extensions/mcp.py`：连接器配置编辑从「创建者或超管」放宽为「能管理该连接器的人」，认证仍只能整体重置，传输方式仍限超管。
- 前端：`SubscriptionPanel.vue` 增加「知识库权限」类型标签，目录展示建库权限条目。

## 替代方案

- 新增独立「建库权限表」：多一张表、多一套接口，收益仅是一次性布尔授权，复用订阅表足够。
- 给普通用户按部门隔离可见性：与「用户不属于部门、靠申请拿资源」的目标冲突，也会让跨部门协作失效。

## 后果

- 部门管理员能处理本部门范围内的申请，审批队列与角标都只看自己可处理的部分；全平台资源仍只看得到、改不了。
- 建库权限是全局一次性授权，撤销订阅即失去建库能力；已建知识库不受影响。
- 个人知识库默认只有本人可见，需要共享时走知识库自己的共享范围。

## 验证

- `backend/test/integration/api/test_subscription_approval_scope.py`：部门管理员只看到并批准本部门范围申请，其他部门与指定人范围返回 403；建库权限由部门管理员审批返回 403、超管批准后用户可建库、未获批用户返回 403。
- `backend/test/integration/api/test_department_admin_scope.py`：本部门管理员（非创建者）可改连接器非敏感字段，回填请求头 403，别部门管理员拒绝。
- 容器内 `pytest test/unit -m "not slow"` 3790 passed / 148 skipped；契约检查通过；前端 lint/build 通过。
- Playwright 真实页面（5173 + 5050 + PostgreSQL）：普通用户在「扩展 → 订阅」申请「个人知识库创建权限」→ 超管待审批队列批准 → 用户在「扩展 → 知识库」新建个人知识库并打开详情、上传文件成功（1 个文件入列）；部门管理员 `mcp_admin_test` 在连接器页新建连接器（默认本部门共享），详情页可「编辑连接器」修改描述与服务地址并保存，传输方式保持禁用、认证仍只能整体重置。
- 期间修复三处前端管理员守卫：知识库类型/列表/详情/上传接口改为登录用户访问（后端按共享范围校验），知识库详情路由去掉 `requiresAdmin`，个人知识库创建者在前端返回 `can_manage=true`。
