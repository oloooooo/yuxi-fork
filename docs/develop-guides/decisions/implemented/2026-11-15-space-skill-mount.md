# 业务空间以共享 Skill 挂载到子 Agent

状态：implemented
类型：simplification
Owner：backend/yuxi/modules/extensions/skills/runtime.py

## 问题

旧实现为业务空间新增了一条并行链路：`space_slug` 运行配置、`SpaceKnowledgeBackend` 只读对象存储视图、`spaces` 桶、管理员发布页和独立测试。它重复了 Skill 已有的包管理、部门授权、用户投影和只读挂载；只接受 Markdown；空间 Agent 与空间内容仍是两套生命周期。

需求是：一个部门的知识空间对应一个子 Agent，部门成员自动看到并使用它，不为每个用户分配，也不新增一套文件后端。

## 决策

空间不再是一种新对象，而是两件现有资源的约定组合：

- 一个共享 Skill 包，保存空间资料与操作说明，`share_config.read_scope` 指向部门；
- 一个共享 Agent，slug 与 Skill 相同，`config_json.context.skills` 与 `preload_skills` 选中该 Skill，共享范围指向同一部门。

用户按部门同时看到 Agent 与 Skill。运行子 Agent 时，现有 Skill 运行时把 `SKILL.md` 预加载，并把整个 Skill 目录只读投影到 `/home/user/skills/<slug>/`。空间内容更新走 Skill 文件编辑或重新上传；空间角色与边界写在 Agent 系统提示词。

### 实现方案

- **发布**：管理员在“扩展 → Skills”上传标准包，确认共享并把读取范围设为部门。Skill 行、内容目录和投影由现有 `install_draft`/`projection` 维护。
- **装配**：管理员创建共享 Agent，slug 与 Skill 相同，写入 `context.skills=[slug]`、`context.preload_skills=[slug]`，共享范围设为同一部门。`prepare_agent_config_write` 继续按创建者可见资源校验引用；运行时仍按操作者可见资源取交集。
- **运行**：子 Agent 由 `create_session` 以 `run.uid` 创建，`resolve_runtime_skills_for_context` 复核 Agent 可见性，合并选中 Skill 与个人 Skill，`lock_accessible_shared_skills_for_runtime` 过滤不可访问项，`build_runtime_skills` 给出 `/home/user/skills/<slug>/SKILL.md`。沙盒对共享 Skill 目录只读。
- **授权失败边界**：Agent 不可见时 HTTP 404、运行时 `PermissionError`；Skill 不可访问时从运行快照中过滤，Agent 仍可运行但没有该空间资料；Skill 被移出部门或停用后，下一次投影刷新删除目录。
- **删除旧链路**：移除 `space_knowledge.py`、`services/spaces.py`、`api/routers/spaces.py`、`SpacesView.vue`、`spaces_api.js`、`context.space_slug`、`/spaces/` 路由、空间测试与 `backend/test/data/spaces/*` 夹具。旧决策记录由本记录取代。
- **文档**：新增 `docs/agents/business-spaces.md`，说明命名、发布、装配、迁移和限制；Skills 文档与导航增加入口。

## 替代方案

1. **保留 `/spaces` VFS，只把内容改成 Skill 包**：仍需维护第二套只读后端、桶、路由和页面，且无法复用 Skill 的部门授权与投影。拒绝。
2. **按部门自动加载所有空间 Skill，不让 Agent 显式选择**：需要新的“部门 → Skill”隐式规则，模型输入和预加载范围不再由 Agent 配置可审计地决定；也会绕过现有 `skills` 选择语义。拒绝。
3. **每个空间用一个 Agent 专属 Skill**：专属 Skill 固定一 Agent 一个、slug 为 `self-UUID8`、生命周期跟随 Agent，无法让同一 Agent 按部门加载多个空间，也不能把内容独立发布给部门。拒绝。
4. **每个空间一个独立对象类型和发布页**：即旧方案，重复已有能力。拒绝。

## 后果

- 不新增运行时机制、对象类型或页面；空间配置由两个已有资源的共享范围组成。
- Agent 与 Skill 的部门范围是两个事实，必须同时维护。运行时不自动继承，只取交集；范围不一致时空间 Agent 仍可见，但任务无法完成。文档已说明。
- 共享 Skill 源目录由 `YUXI_SKILL_DATA_DIR` 指定，投影目录由 `YUXI_SKILL_PROJECTION_DIR` 指定，沙盒节点以 `CUBE_NODE_SKILL_PROJECTION_DIR` 只读挂载投影根；当前是共享卷，不是对象存储。多主机部署必须让 API/Worker 与沙盒节点挂载同一源卷/投影卷；PRD 5.8.1 要求的多实例一致对象存储视图尚未提供。
- Skill 包可含脚本，容量上限为 50 MiB、2000 个条目，上传 ZIP 10 MiB；旧空间只收 Markdown，迁移需检查内容。
- 旧 `spaces` 桶中的数据不会自动迁移；需要按文档整理为 Skill 包后重新上传，再清理旧桶。
- 删除 VFS 及其测试，减少一条需要独立维护的读取链路。

### 限制

- **多实例一致性**：共享卷不是对象存储；跨主机部署需要额外保证同一 `YUXI_SKILL_DATA_DIR`、`YUXI_SKILL_PROJECTION_DIR` 与沙盒节点 `CUBE_NODE_SKILL_PROJECTION_DIR`。若无法保证，需要优先把 Skill 源迁移到共享对象存储，而不是恢复 `/spaces` VFS。
- **授权漂移**：Agent 与 Skill 的部门范围分开维护，缺少跨资源校验。当前用运行时交集和文档约束；范围不一致时空间 Agent 仍可见，但提示词中的空间路径不存在，任务无法完成。若误配频繁，再考虑在 Agent 保存时增加“所选 Skill 必须覆盖 Agent 共享范围”的显式校验。
- **旧 Run 配置**：删除 `context.space_slug` 后，旧 `input_payload.context_snapshot` 中的该字段会被忽略；部署时需结束或重跑仍依赖 `/spaces` 的进行中 Run。
- **内容面扩大**：Skill 可携带脚本，发布审核范围比只读 Markdown 大；管理员需按现有 Skill 审核流程检查上传包。
- **投影成本**：每个模型/工具边界刷新用户投影会检查并复制可访问 Skill 目录；空间资料较大时需关注 I/O。现有 Skill 机制已承担该成本，当前实现不新增放大点。

## 验证

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
|---|---|---|---|---|---|
| 部门成员同时可见空间 Agent 与 Skill | Agent/Skill 可见性查询 | `agents/repositories/definitions.py`、`skills/access.py` | `test_department_space_skill.py`：user0 GET agent 200，accessible 含 slug | 其他部门 user1 GET agent 404、accessible 不含 slug | Passed |
| 空间 Agent 运行时解析到空间 Skill 并预加载 | 运行时 Skill 解析 | `skills/runtime.py` | 同一测试：以 `agent_slug` 调用 `resolve_runtime_skills_for_context`，`effective_skills`、`preloaded_skills` 均为 `[slug]`，`preloaded_skill_contents[slug]` 含 `# <slug>` 且不含 `references/rules.md` 正文 | user1 因 Agent 不可见被 `PermissionError` 拒绝 | Passed |
| 空间资料投影到用户 Skill 目录 | 投影 | `skills/projection.py` | 同一测试：host 投影目录下 `references/rules.md` 可读 | user1 投影目录不存在；撤销部门授权后 user0 目录被删除 | Passed |
| Agent 与 Skill 授权不一致时按交集降级 | 运行时权限交集 | `skills/access.py` | 同一测试：Agent 仍可见、Skill 移出部门后快照为空 | 不重新授权时不得回退读取旧投影 | Passed |
| 旧 `/spaces` 能力不存在 | 删除后的路由与引用 | 已删除文件 | `rg -n "space_slug" backend/yuxi`、`rg -n "SpaceKnowledge" backend/yuxi`、`rg -n "SpacesView" frontend/src` 均无命中；`GET /api/spaces` 404 | 旧页面/路由不得再返回 200 | Passed |
| 文档可导航 | 文档构建与相对链接 | `docs/agents/business-spaces.md` | `pnpm run build`（docs）成功；相对链接检查通过 | 断链检查失败 | Passed |

旧能力不存在：`/spaces` HTTP 路由、`SpaceKnowledgeBackend`、`spaces` 对象存储发布链路、`SpacesView` 管理页和 `context.space_slug`。

重新引入条件：出现 Skill 投影无法表达的只读大数据集、需要与 Skill 生命周期分离的空间发布流程，或空间内容必须直接由对象存储提供多实例一致视图而不经过共享卷。

测试：`backend/test/integration/api/test_department_space_skill.py` 在已配置 `TEST_USERNAME`/`TEST_PASSWORD` 的集成环境执行，结果为 `1 passed`；`GET /api/spaces` 返回 404 也由该测试覆盖。

只读写入拒绝沿用共享 Skill 挂载，由 `backend/test/unit/sandbox/test_user_sandbox_backend.py::test_shared_skills_read_only_without_vm` 覆盖；本次未新增写入用例。

本用例验证到 resolver 与 host 投影层；实际模型请求中的 `<preloaded_skill>` 注入、沙盒 `/home/user/skills` 读取未在本次用例中重跑，沿用既有 Skill 中间件与沙盒测试。

后端 unit 全量：`docker compose exec api uv run --no-sync --group test pytest test/unit -m "not slow" -q` → `3766 passed, 127 skipped`。
