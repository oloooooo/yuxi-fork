# 决策：资源订阅申请与超级管理员审批

状态：implemented
类型：feature
Owner：backend/yuxi/modules/identity/subscriptions/service.py

## 问题

连接器、智能体、Skill 的可见性由各自 `share_config` 的共享范围决定。当普通用户需要某个未对其共享的资源时，没有任何沟通通道：申请人看不到资源是否存在，管理员也不知道谁在等审批，只能线下沟通后由管理员手改共享范围。

## 决策

- 新增查看型资源目录：普通用户可以浏览连接器、智能体、Skill 的名称、描述与标识（`GET /api/subscriptions/catalog`），目录只做展示，不返回连接地址、请求头等敏感配置。
- 申请人只能"申请订阅"，不能修改共享范围；审批权只给超级管理员（`get_superadmin_user`）。审批通过写入独立订阅表，不修改资源自身的 `share_config`。
- 权限解析取并集：`resolve_*_permission` 增加 `granted` 参数，只在原判定为 `NONE` 时提升为 `READ`；原本有 `MANAGE` 的不受影响，降级共享范围也压不住已批准的订阅。
- 私有智能体不出现在目录、不允许申请（`私有智能体不支持订阅`），避免把私有资源变成可申请对象。
- 同一资源同一用户只保留一条记录：`pending`/`approved` 重复申请幂等返回，`rejected` 重新申请回到 `pending`。
- 超管不能审批自己的申请（403），已处理记录不能重复审批（409），撤销订阅删除记录后立即失去授权。
- 通知只做页面角标：待审批数量显示在扩展页「订阅」标签上，不引入站内信或外部通知。
- 订阅表随 business Schema 升到 v5（`resource_subscriptions`，唯一约束 `(resource_type, resource_slug, user_id)`），升级脚本 `python -m yuxi.migrations.resource_subscriptions`。

## 替代方案

- 直接改 `share_config`：会把一次性授权写进共享范围，撤销与审计都失去边界，且申请人无法自助发起。
- 给每个资源加申请人列表：三种资源各写一份，同一事实分散在三个 Owner。
- 站内信/邮件通知：需要额外的消息投递与未读模型，当前只有"看角标"的真实需求。

## 后果

- 授权事实集中在 `resource_subscriptions`，资源删除后订阅记录成为悬空引用，审批列表用 `resource_missing` 标注，目录自动消失。
- 管理员看不到"我被申请了什么"，只有超管有审批入口；多名超管共享同一队列。
- 可见性判定在列表与审批两处都依赖 `approved_slugs`，同一请求内用 session 级缓存避免逐条查询。
- 申请目录会暴露资源名称与描述给所有登录用户；这是"可申请资源必须可被发现"的代价，链接地址与凭据仍然只有管理者可见。

## 验证

- `backend/test/integration/api/test_subscriptions_api.py`：真实 HTTP 覆盖申请→批准→连接器可见→撤销后消失、驳回后可重新申请、已可见资源拒绝申请、私有智能体拒绝、共享智能体批准后可读。
- `backend/test/integration/services/test_schema_migration_version.py::test_resource_subscriptions_upgrade_creates_table_and_is_idempotent`：真实 PostgreSQL 证明 v4→v5 建表、可重复执行、未知版本拒绝。
- 全量 `pytest test/unit -m "not slow"`：3787 passed / 148 skipped。
- 真实页面（Playwright）：普通用户提交申请后目录显示「待审批」；超管「订阅」标签角标从 7 变 6，待审批列表批准后，用户刷新看到「已可用」。
