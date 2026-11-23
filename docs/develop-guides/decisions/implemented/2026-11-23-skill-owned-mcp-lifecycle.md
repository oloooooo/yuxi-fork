# 决策：按 Skill 归属管理专有连接器

状态：implemented
类型：feature
Owner：backend/yuxi/modules/extensions/mcp/service.py

## 问题

Skill 通过 `mcp_dependencies` 声明所需连接器，但连接器本身是全平台共享资源：没有来源标记，删除 Skill 后连接器留在列表里，管理员无法区分“这个 Skill 建的”和“人工建的”，重装同名 Skill 生成 `-v2` 后归属也会丢失。

## 决策

- 安装共享 Skill 时，前端把补建弹窗中新建的标识随确认请求回传，后端只把“本次安装者创建、且属于该 Skill 依赖”的连接器记为 `origin_skill`。
- 安装前已存在的连接器不标记，删除 Skill 时不受影响。
- 删除 Skill 时：仍有其他 Skill 引用则保持原状；被智能体显式引用则保留；都没有则停用而不是删除，且不自动恢复启用（避免绕过管理员的停用意图）。
- 归属只由安装流程写入，不提供手工“转正/认领”。

## 实现方案

- `mcp_servers.origin_skill` 可空列；业务 Schema 由 v3 升到 v4，迁移脚本 `backend/yuxi/migrations/mcp_origin_skill.py` 只加列和索引，存量连接器保持空值。
- 新增 `mark_skill_owned_mcp_servers`：按 `sorted()` 顺序加锁，只认领「传入的 `created_mcp_slugs` ∩ 该 Skill 依赖 ∩ 非内置 ∩ `origin_skill` 为空 ∩ `created_by` 与操作人一致」的连接器，不改写启用状态。
- 新增 `release_skill_owned_mcp_servers`：只处理 `origin_skill` 等于被删 Skill 的连接器；仍有 Skill（排除自身）或智能体引用时保留，否则停用；不自行提交事务，由删除用例统一提交。
- `confirm_skill_install_draft` 增加 `created_mcp_slugs`，循环结束后统一标记并提交；标记阶段失败只记录日志，不回滚已安装结果，也不阻塞草稿消费。
- Skill 共享范围变更只同步到 `origin_skill` 等于该 Skill 的连接器，且要求操作人对连接器有 MANAGE 权限。
- 新增只读依赖查询 `list_mcp_dependents` 与 `GET /api/system/mcp-servers/{slug}/dependents`（仅管理员，普通用户看不到他人的私有智能体名称），详情页展示来源与依赖者。

## 替代方案

- 用草稿创建时间窗推断归属：任一管理员在窗口内建的连接器都会被误标，且前端重建草稿后 `created_at` 晚于连接器，会永久漏标。
- 连接器与 Skill 多对多关联表：能表达一个连接器被多个 Skill 共有，但需要迁移、维护同步和额外清理逻辑，与“按 Skill 安装一次”的实际用法不成比例。
- 删除 Skill 时直接删除专有连接器：不可逆，误删后要重新申请凭据。

## 后果

- 仅当连接器由本次安装者在安装过程中新建时才算专有；手工预先创建的同名连接器保持公有语义。
- 多个 Skill 共用一个专有连接器时，只要还有引用就保持启用；归属不转移，最后一次引用删除后才停用。
- 被智能体引用的连接器不会因删除 Skill 而停用，运行时会继续按连接器状态工作。
- 未传 `created_mcp_slugs` 的旧客户端不会标记任何连接器，行为退化为“全部公有”。
- 管理员仍可手工编辑、启停、删除这类连接器；详情页只提示依赖关系，不阻止操作。连接器被停用后，重装同名 Skill 不会自动恢复，需要管理员手动启用。

## 验证

- 单元测试 `backend/test/unit/services/test_mcp_skill_ownership.py`：认领自己的新建连接器、未在本次新建列表则不标记、内置跳过、他人创建不标记、仍有其他 Skill 引用时保持启用、智能体引用不停用、无引用停用、归属他人时不被误停用。
- 集成测试 `backend/test/integration/services/test_schema_migration_version.py`：v3→v4 幂等加列、未知版本在 DDL 前拒绝、业务 Schema 版本断言为 4。
- 手工真实 HTTP 验证（不入库，`.yuxi/` 被忽略）：首次安装后 `origin_skill` 写入且启用；`/dependents` 返回依赖 Skill；重复安装得到 `-v2` 后删除首个 Skill 仍启用；删除 `-v2` 后停用。
- 浏览器验证：连接器详情页显示“由 Skill「ui-depend-skill」引入”与“被 1 个 Skill、0 个智能体引用”，删除确认框附“删除后会影响：Skill「界面依赖测试」”。
- 未覆盖：管理员（非超管）在 UI 补建连接器的整条路径、并发安装/删除、真实 HTTP 的 `created_mcp_slugs` 与 `/dependents` 权限回归仍需补集成测试。
