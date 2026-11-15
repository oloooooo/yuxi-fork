# 业务空间落在部门授权，空间知识以对象存储前缀上的只读 VFS 挂载到 Agent 文件后端

状态：proposed

类型：architecture

Owner：backend/yuxi/modules/agents/runtime/sandbox/backend.py

分工：部门授权的判定事实属于 `backend/yuxi/modules/identity/permissions/resource_permission.py`，部门存在性属于 `backend/yuxi/modules/identity/repositories/departments.py`；空间 Agent 的可见性名单事实属于 `backend/yuxi/modules/agents/repositories/definitions.py` 的 `list_visible`（`backend/yuxi/modules/agents/services/directory.py` 只是薄包装）；对象读写属于 `backend/yuxi/infrastructure/minio/client.py`。本方案新增的模块是 `backend/yuxi/modules/agents/runtime/sandbox/space_knowledge.py`（只读前缀视图，读取侧）与 `backend/yuxi/modules/agents/services/spaces.py`（发布侧：slug/路径校验、写前缀、列举与清空），接口在 `backend/yuxi/api/routers/spaces.py`，页面是 `frontend/src/pages/SpacesView.vue`；字段与选项注入属于 `backend/yuxi/modules/agents/runtime/context.py`（`space_slug` 配置项）与 `backend/yuxi/modules/agents/runtime/base.py`（选项填充）；它们只组合上述既有事实，不复制它们的判定。本记录声明组合方式，不作为运行时事实源。

## 问题

业务侧需要"一个底座服务多个业务空间"：主管 Agent 拆解任务、按空间选择子 Agent、汇总结果；每个空间有独立的知识、工具与技能配置。空间的新增成本必须接近"加目录 + 加一条授权记录"，不改主流程。

已确认的口径：

1. 空间授权落在部门。一个空间授权给一个或多个部门，部门内普通用户自动继承该空间的可见与可用范围；跨部门互不可见。
2. 空间知识不走向量检索。Agent 按路径直读原文，向量检索只服务个人知识库。
3. 空间内容由开发者构建，不经过业务方的上传流程，也不走知识库的解析/切块/索引链路。业务方不上传 Markdown，也不在对话里选知识库。
4. 空间知识以 Markdown 文件集合的形式存放在对象存储（MinIO/S3）的空间前缀下，Agent 通过虚拟文件系统接入。

现有能力（已核对源码）：

- 空间可以作为 Agent 记录承载：`agents` 表已有 `slug`、`backend_id`、`config_json`、`share_config`、`visibility` 字段，`config_json` 承载上下文、模型、工具、技能配置。
- 授权模型已能表达部门：`backend/yuxi/modules/identity/permissions/resource_permission.py` 的 `share_config` v2 支持 `read_scope.access_level="department"` 与 `department_ids`（`_normalize_scope` 已强制非空），`scope_matches` 按 `users.department_id` 判定，`AGENT_PERMISSION_POLICY.role_ceiling` 让普通用户封顶 READ。
- 名单实时性已具备：`backend/yuxi/modules/agents/services/cooperation.py` 的 `list_agents` 走 `backend/yuxi/modules/agents/services/directory.py` 的 `list_public_agents`，最终由 `backend/yuxi/modules/agents/repositories/definitions.py` 的 `list_visible` 实时计算；子会话在 `create_session` 时只冻结可配置快照，授权结果不进快照。
- 挂载点已存在：`backend/yuxi/modules/agents/runtime/sandbox/backend.py` 的 `create_agent_composite_backend` 构造 `CompositeBackend(default=UserSandboxBackend(...), routes={...})`；`origin/main` 上 `routes` 为空，本变更新增 `/spaces/` 一条。deepagents `CompositeBackend` 按路径前缀最长匹配路由，剥离前缀后交给子后端，并对 `ls` 返回的 `FileInfo.path` 补回前缀。
- 对象存储原语够用，无需新客户端：`backend/yuxi/infrastructure/minio/client.py` 已有 `alist_object_metadata(bucket, prefix)`（递归列出前缀下全部对象）、`adownload_file(bucket, object_name, max_bytes)`、`astat_file(bucket, object_name)`、`ensure_bucket_exists(bucket)`。
- `context.space_slug` 是配置项，在装配点可见（`backend/yuxi/modules/agents/runtime/context.py` 的 `BaseContext.space_slug`），因此子后端由“当前 Agent 绑定的空间”直接决定前缀，不需要信任任何外部路径参数。选项由 `backend/yuxi/modules/agents/runtime/base.py` 的 `get_info` 从已发布前缀填充。

缺口只有一个：没有任何文件后端把"某个对象存储前缀"呈现成可浏览的目录树。知识库模块能列出的是 DB 目录树（`knowledge_files.parent_id`）与解析产物，与本口径（开发者构建的 md 集合、不入知识库）不是同一条链路，不能复用。

非目标（本次不做）：

- 不新增独立的空间表、空间授权表或服务身份体系；子 Agent 仍以当前用户身份运行。
- 不经过知识库：不建知识库记录、不做解析、不切块、不索引。因此不存在"空间知识被误索引"的问题（详见"为什么不走知识库"）。
- 不改个人知识库的混合检索链路；不实现 PRD 的表格处理、编程式工具调用、会话配额、子 Agent 级人工确认。
- 不做业务方上传入口与格式转换：空间内容由开发者构建（把资料转成 Markdown 后写入前缀）。
- `grep` 只做字面匹配且只在空间内的 Markdown 上做：不建发布期索引、不做正则、不做跨空间检索。
- 不做目录物化：不把对象同步成本地目录副本，因此没有副本与源不一致、同步延迟或宿主机路径暴露。

依据说明：本记录的口径来源是仓库根目录的 `Agent-PRD-2026-001-V2.2.md`，该文件被 `.gitignore` 的 `/Agent-PRD-*.md` 排除、不入版本库；需要长期引用时，应把对应需求段落另存为可跟踪文档。

## 提案

### 为什么不走知识库

把空间知识放进知识库会连带一整套与本口径无关的机制：文件状态机（`parsing`/`parsed`/`indexed`）、解析重试、只读连接器类型（飞书/Notion/Dify 不支持文档读取）、"是否自动入库"的纪律、以及事后无法证明"这个库没被索引"的持久事实缺口。空间内容是开发者构建的静态 md 集合，直接用对象存储前缀表达即可：`ls` 是一次前缀列举，`read` 是一次对象下载，切片与索引都不存在。

### 实现方案

本轮只落地第 4–6 步（对象布局、只读视图、挂载）；第 1–3 步中“空间即 Agent 记录”“部门授权”“名单实时”都是既有能力，“部门存在性校验”尚未实现，因此验收矩阵前 3 行为 Not run。

1. 空间即 Agent 记录。用 `agents` 表的一条记录表达一个业务空间：`config_json` 承载该空间的上下文、模型、工具、技能配置，`share_config` 承载部门授权。空间标识是发布页里创建的前缀名，Agent 通过配置项「业务空间」（`context.space_slug`）显式绑定它，不依赖 slug 命名约定：新增空间只新增一条记录、选一次「业务空间」、授权部门，并把内容写进对应前缀，不改主流程代码。前置条件是管理员身份：普通用户创建共享 Agent 已被拒绝(`backend/yuxi/modules/agents/repositories/definitions.py`、`backend/yuxi/modules/agents/services/definitions.py`)。

2. 部门授权复用现有可见性模型。空间 Agent 的 `share_config` 使用 v2 结构，`read_scope.access_level="department"` 且 `department_ids` 列出授权部门，`visibility="shared"`。普通部门成员经 `scope_matches` 的 department 分支得到 READ（`role_ceiling` 封顶），非授权部门按无权限处理。补一条缺失的校验：`backend/yuxi/modules/identity/repositories/resource_grants.py` 的 `validate_shared_grants` 只校验 `manage_scope` 的 user 分支，不校验 `department_ids` 中的部门是否存在（非空已由 `_normalize_scope` 强制），写错 id 会让空间静默不可见；在该函数内用 `backend/yuxi/modules/identity/repositories/departments.py` 的 `list_departments` 校验并显式失败。该函数是全部 `share_config` 写路径的收敛点，改此一处即覆盖 Agent、知识库与技能。

3. 子 Agent 名单沿用实时计算。主管在装配期调用既有 `list_agents` 得到当前用户可见的空间 Agent 名单，请求结束即回收。选址复核保持在 `backend/yuxi/modules/agents/services/cooperation.py` 的 `create_session`：`selected_agent_id` 取显式 `agent_id` 或当前 Run 的 `agent_slug`，随后经 `AgentRepository.get_visible_by_slug(..., user=user, for_key_share=True)` 复核，返回 `None` 即拒绝。`agent_id` 参与选址但必须经此复核，请求自带的空间声明不改变判定结果。

4. 对象布局。新建专用私有桶 `spaces`（由发布流程创建，例如 `mc mb` 或调用既有 `ensure_bucket_exists`；不放进 `PUBLIC_READ_BUCKETS`，装配期不建桶），键格式 `{space_slug}/{相对路径}`，例如 `sales-2026/policies/报价规则.md`。不复用 `knowledgebases` 桶：该桶的对象布局属于知识库（`{kb_id}/uploads|parsed|...`），前缀清理、生命周期与权限都按知识库语义定义，混用会让"删知识库"这类批量清理波及空间内容。内容由开发者在发布时写入（`mc cp`、控制台或部署脚本），版本化、备份与生命周期沿用对象存储自身能力。业务方不参与上传，也不选知识库。

5. 新增只读前缀视图 `backend/yuxi/modules/agents/runtime/sandbox/space_knowledge.py`，构造入参只有 `space_slug`（取自 `context.space_slug`），只实现 `als`/`aread`/`aglob`/`agrep` 与写入侧拒绝：

   - 返回类型按 deepagents 协议区分：`LsResult`/`ReadResult`/`GlobResult` 是 dataclass，其 `entries`/`matches` 里的 `FileInfo`/`GrepMatch` 是 TypedDict dict（`CompositeBackend` 用 `fi["path"]` 下标访问并补前缀，做成 dataclass 会 `TypeError`）。
   - 子后端看到的路径是剥离前缀后的 `/` 与 `/dir/file.md`（`CompositeBackend` 负责转换），`FileInfo.path` 必须返回相对挂载点的库内路径，否则模型会看到 `/spaces/spaces/...`。
   - 前缀由 `space_slug` 决定，不接受模型或请求传入的绝对路径拼接：路径先规范化后必须落在 `{space_slug}/` 之下，越界（`..`、绝对路径、编码绕过）直接返回 `error`，因此跨空间读取不可能。
   - `als(path)` 用 `alist_object_metadata(bucket, f"{space_slug}/{子路径}")` 一次取回该子树的键，在内存里按 `/` 分组出直接子目录与文件；对象存储的目录标记键（键等于前缀或以 `/` 结尾）不算条目，非 `.md` 键不列出。`aglob` 复用同一次列举结果，匹配交给 deepagents 的 `compile_recursive_glob`（与其它后端同一套 glob 语义：无 `/` 的模式按 basename 匹配、`**` 可匹配零层、前导 `/` 只收窄不放宽）。
   - `aread(path, offset, limit)` 用 `adownload_file(..., max_bytes=单文件上限)` 取原文，再交给 deepagents 的 `slice_read_response` 切窗口，分页字段（`total_lines`/`start_line`/`end_line`/`next_offset`）与其它后端一致：读到文末时 `next_offset=None`，`offset` 超出文件长度返回 `error`，`limit<=0` 返回 `no_lines_requested=True`。原文不含行号前缀，无需剥离。
   - 只服务 UTF-8 Markdown（后缀大小写不敏感，保证“列得出就是读得到”）：非 `.md` 后缀、非 UTF-8 正文与单文件超限返回稳定 `error`（如“空间知识只提供 UTF-8 Markdown 文件”），避免中间件按后缀把内容当多模态 base64 塞进上下文，也避免列出模型永远打不开的文件。内容格式由开发者在发布时保证，实现侧只做拒绝。
   - 写入侧 fail-closed：`write`/`edit`/`delete` 与批量上传/下载（含异步版）返回带 `error` 的结果（文案 `空间知识是只读挂载：写入、编辑、删除与上传一律拒绝。`，批量接口为 `PERMISSION_DENIED`），不落盘、不报成功；`aread` 的失败同样返回 `error`，不返回空正文。
   - `agrep` 逐个文件容错：单个对象读不动或不是 UTF-8 时只丢该文件，其余结果照常返回，不把整次检索变成 `error`。
   - 同步方法只作协议占位（基类不是 `@abstractmethod`，未实现即 `NotImplementedError`；当前装配与调用链全部走异步）：同步 `ls` 返回空 `entries`、同步 `glob` 返回空 `matches`，只有写入侧与 `read` 返回 `error`——空结果不会像 error 那样在 `CompositeBackend` 合并时打挂整次调用。
   - 不设实例内缓存：每次调用现场列举/下载，对象前缀变更立即生效，也没有跨调用状态需要维护。

6. 挂载工厂保持同步，只多一行路由。`create_agent_composite_backend` 不改成异步函数、不新增装配期 IO，只在 `routes` 中加一条 `"/spaces/"`：

   - `context.space_slug` 非空时挂载，前缀就是它。为空（未绑定空间的 Agent）时不挂载，`/spaces` 落到 default 后端并按普通路径失败——这是既有行为，不需要额外分支。
   - 前缀没有对象、甚至 `spaces` 桶尚不存在时，`als`/`aglob`/`agrep` 一律返回空结果，不返回 `error`：`CompositeBackend` 在根级 `glob`/`grep` 合并时会因任一子结果带 `error` 而使整个调用失败，把 default 后端（工作目录）的结果一并丢掉。"没有空间知识"是事实而非操作失败，且首日未建桶不能让所有 Agent 的根级检索炸掉。
   - 后果：没有空间内容的 Agent 也会看到空 `/spaces` 目录。提示词由空间包的 `AGENT.md`（发布者编写）提供，平台不向 system prompt 注入 `/spaces` 说明；目录为空时模型看到的是空列表，这一点由发布流程保证"该 Agent 有内容"来规避。
   - 本步只改挂载这一处，`backend/yuxi/modules/agents/runtime/agent_backends/chatbot/graph.py` 与 `backend/yuxi/modules/agents/services/compression.py` 两个调用点无需改动，测试里对 `create_agent_composite_backend` 的既有 9 处 `monkeypatch.setattr` 替换也因签名不变而保持有效。唯一的测试侧副作用是 `backend/test/unit/sandbox/test_user_sandbox_backend.py` 里手工构造的 `SimpleNamespace` 上下文，必须补上 `space_slug=None`（真实 `AgentRuntimeContext` 恒有该字段）,否则读取该属性时 `AttributeError`。

7. 入口分工。知识 MCP 工具继续服务**个人知识库**的检索与定位（`search_file` 按文件名、`find_kb_document` 文档内定位含 regex、`open_kb_document` 按行窗口直读）；`/spaces` 服务**空间知识**的目录浏览、按路径读取与空间内字面检索。两者对象不同、不重叠，提示词说明"空间资料在 `/spaces/<路径>`，个人知识库用知识工具"，模型不需要在两个入口间做语义选择。空间内的检索用 deepagents 自带的 `grep_matches_from_files` 实现，语义（字面匹配、`max_count` 截断标记）与其它后端一致。

8. `execute` 不构成绕过。deepagents 的 `CompositeBackend` 不路由 `execute`，它永远交给 default 后端；default 是 `UserSandboxBackend`，只能在用户 VM 与工作目录内执行命令，VM 内不存在空间知识的任何副本。因此 shell 无法读写空间内容，只读约束只由第 5 步的后端返回错误承担。

### 构建与运维前提

- 空间内容的构建步骤是：把资料转成 Markdown → 按目录组织 → 在 `/spaces` 管理页（`admin`/`superadmin`）拖入空间文件夹发布 → 建/改一条 Agent 记录并授权部门。发布是唯一需要写对象存储的动作，因此收敛到这一条管理员入口；`spaces` 桶在首次发布时由 `upload_file` 的 `ensure_bucket_exists` 自动创建，运行期的读取端不建桶。
- 发布入口：`GET /api/spaces`（空间清单）、`GET /api/spaces/{slug}`（已发布路径）、`POST /api/spaces/{slug}/files`（multipart，`paths` 与 `files` 一一对应）、`DELETE /api/spaces/{slug}`（清空前缀）。前端页面是 `frontend/src/pages/SpacesView.vue`（路由 `/spaces`，导航项仅管理员可见）。发布侧校验：slug 必须与 `AgentRepository._slugify` 同形（小写字母、数字、下划线和连字符，最长 56），路径必须是空间内相对 `.md`，非法路径整批拒绝；桶尚未建立时列举返回空而不是报错。
- `spaces` 前缀只收 `.md`，这是**发布硬约定**：仓库不提供格式转换，转换责任在构建方；实现侧对其它后缀返回稳定错误（见实现方案第 5 步），违规内容不会被列出也读不出来，但也不会被自动纠正。
- 空间内容变更不需要重启或重新部署：`ls`/`read` 每次现场读取对象存储。
- 输出目录约定：空间子 Agent 的产物统一写 `outputs/{space_slug}/...`。同一 Project 下父 Run、子 Run 与并发子 Agent 共享同一 workdir，且写入是覆盖语义（`backend/.venv/Lib/site-packages/deepagents/backends/filesystem.py:489` 的 `write` 在文件存在时覆写），引擎不做同名保护；用子目录把并发写入错开。该约定只写在空间包的 `AGENT.md`（发布者编写）里，代码不校验。
- 空间下线只删 Agent 记录或撤销部门授权；对象保留，便于恢复。前缀与 Agent 记录的一致性由发布流程保证，代码不做对账。

### 边界与发布点

- 授权判定仍在后端可见性查询与依赖注入处执行，前端守卫与提示词不作为授权边界。前缀由 `context.space_slug` 决定，模型无法用路径参数指向其他空间；`create_session` 的 `get_visible_by_slug` 复核是进入某一空间的唯一闸门。
- 路径语义单一：`/spaces/...` 只在本次 Run 的 `CompositeBackend` 内解析，内容经对象存储读取，不映射宿主机路径。它**不是** runtime 虚拟根：`backend/yuxi/modules/workspace/runtime_paths.py` 的 `is_runtime_path` 只认 `/home/user/workspace` 与 `/home/user/skills`，因此 `/spaces/...` 不会获得工作目录预览与产物路径能力——这是有意的，空间内容不属于工作目录。
- 只读约束在子后端层 fail-closed：写入与编辑返回错误结果而不是落盘后报错；`delete` 工具本就不在 Yuxi 的文件工具白名单内。
- 对象缺失、下载失败、非 Markdown 后缀、非 UTF-8 正文、路径越界都返回稳定 `error`，不伪造为空内容；单文件读取有大小上限（`MAX_FILE_BYTES`），单文件超限同样返回 `error`。
- 空间前缀不可用（桶未建、列举失败）不会传播成根级 `glob`/`grep` 的失败：这些路径按空空间处理，只影响 `/spaces` 这一条路由的结果，同 Run 的工作目录、技能与其他能力不受影响。

## 替代方案

| 方案 | 收益 | 不采用的原因或代价 |
| --- | --- | --- |
| 空间知识放进知识库，再用只读后端呈现 `knowledge_files` 目录树（上一版方案） | 复用可见性交集与既有读取原语 | 连带引入文件状态机、解析重试、只读连接器类型、"是否自动入库"的纪律与"无法证明未索引"的持久事实缺口；本口径下内容是开发者构建的静态 md，这些机制都不需要 |
| 只用现有知识 MCP 工具（`open_kb_document` 按行窗口直读、`find_kb_document` 文档内定位、`search_file` 按文件名检索） | 零改造 | 以 `kb_id + file_id` 寻址，需要先把空间内容做成知识库记录（即上一行全部代价）；本口径下空间内容根本不在知识库里，这套工具用不上 |
| 新增独立 `spaces` 表与空间授权表 | 空间与 Agent 解耦 | 现有可见性模型已能表达部门授权；新增表会带来两条并行的授权判定路径与两处事实来源 |
| 复用 `knowledgebases` 桶下的 `spaces/{slug}/` 前缀 | 少建一个桶 | 与知识库对象布局共享清理、生命周期与权限语义，任何按库前缀的批量删除都可能波及空间内容；隔离一个桶的成本只有一次创建 |
| 把空间内容同步成用户 VM 内的本地目录 | 可直接复用 deepagents `FilesystemBackend` | 需要物化与失效机制，带来副本不一致、装配期同步延迟，并把宿主机路径引入模型可见语义；`FilesystemBackend` 也没有只读开关 |
| 引入独立服务身份运行子 Agent | 子 Agent 不持终端用户凭证 | 需要凭证签发与轮换链路，超出本次范围 |
| 用 `agrep` 在 `/spaces` 内做内容检索 | 目录视图自足，模型不必先猜文件 | 每次检索把该空间范围内全部 Markdown 载入内存再匹配。挂载粒度是单个空间前缀，成本与部门空间数量无关；单个空间涨到上千文件时需改为发布期前缀索引 |
| 发布期另建一份空间路径索引（索引文件列出每个文件与其主题） | 模型按索引直接定位，不检索正文 | 索引是第二份事实来源，与文件漂移后会让模型漏读；现有的 `index.md` 导读与 `ls` 已覆盖"有哪些文件"，`grep` 覆盖"某个词在哪份文件"，再建索引没有当前消费者 |

## 验收标准

当前结果含义：Passed 表示已由真实执行验证；Inspected 表示已读源码或文档确认但未执行；Not run 表示本轮未执行；Inferred 表示由既有契约推导。证据列末尾注明本轮未执行的原因与"新增"标记，负向案例列只写失败面。

| 验收主张 | 失败面 | 语义 Owner | 直接证据 / 命令 | 负向案例 | 当前结果 |
| --- | --- | --- | --- | --- | --- |
| 部门成员可见该部门授权空间的子 Agent，非授权部门成员不可见 | 空间授权过宽或过窄 | backend/yuxi/modules/identity/permissions/resource_permission.py（判定）× backend/yuxi/modules/agents/repositories/definitions.py（`list_visible` 输出） | `pytest backend/test/integration/api/test_permission_convergence.py::test_shared_grants_role_ceiling_and_system_configuration` 已存在但本轮未执行；再新增一条断言 `list_agents` 输出集合（新增，需真实 PostgreSQL） | 用户所属部门未授权空间 X，`list_agents` 仍返回空间 X 的 Agent | Not run |
| 子 Agent 名单每次请求实时计算，授权撤销后下一次调用即失效 | 授权变更后旧名单继续生效 | backend/yuxi/modules/agents/repositories/definitions.py | `list_visible` 每次调用实时计算（`backend/yuxi/modules/agents/services/directory.py` 只是薄包装）；行为用例：撤销部门授权后再次调用 `list_agents` 断言结果集变化（新增）；本轮仅读源码 | 撤销授权后同一会话 `list_agents` 仍返回旧空间 | Inspected |
| 写入部门授权时，不存在的部门 id 显式失败而不是静默不可见 | 配错部门导致空间无人可见且无报错 | backend/yuxi/modules/identity/repositories/resource_grants.py | `pytest backend/test/unit/permissions -k department_exists`（校验与用例均新增，本轮未写） | 写错部门 id 仍保存成功，之后空间无人可见且无提示 | Not run |
| `ls` 按对象键列出空间目录树，子目录与文件区分正确且只列 Markdown | 目录列不出、层级错乱，或列出模型永远打不开的文件 | 新增：backend/yuxi/modules/agents/runtime/sandbox/space_knowledge.py（数据源 backend/yuxi/infrastructure/minio/client.py） | `pytest backend/test/unit/agents/test_space_knowledge_backend.py -k space_tree_lists_markdown_only`（用真实夹具 + 手加 `.xlsx` 与目录标记键断言两者都不出现）与 `test/integration/agents/test_space_knowledge_s3.py -k lists_and_reads_the_published_space`（真实 MinIO 桶） | 列出的路径带 `{space_slug}` 前缀，或文件与目录混在同一层，或 `.xlsx` 入口出现在列表里 | Passed |
| `read` 返回对象原文的行窗口，分页字段与其它后端一致 | 内容错位、读到文末仍提示还有后续、越界被伪装成空文件 | 新增：backend/yuxi/modules/agents/runtime/sandbox/space_knowledge.py | `pytest backend/test/unit/agents/test_space_knowledge_backend.py -k "read_paginates_raw_markdown_without_line_numbers or read_to_eof_reports_no_further_pages or read_past_end_is_an_error or zero_limit_reads_no_lines"` | `read` 返回带行号的文本；或文末 `next_offset` 非空；或 `offset` 越界返回空正文 | Passed |
| 非文本后缀、缺失对象、非 UTF-8 与超限文件返回稳定 error，不伪造为空内容 | 二进制被当文本塞进上下文；或存储故障被伪装成"没有内容" | 新增：backend/yuxi/modules/agents/runtime/sandbox/space_knowledge.py | `pytest backend/test/unit/agents/test_space_knowledge_backend.py -k "non_markdown_and_missing_objects_are_refused or oversized_file_is_refused_by_the_read_limit or one_unreadable_object_does_not_abort_content_search or uppercase_suffix_is_listed_and_readable"` | `read` 一个 `.pdf` 返回正文；或对象不存在/超限时返回空字符串；或一个坏对象让整次 `agrep` 变成 error | Passed |
| 路径越界与跨空间访问被拒绝 | 读到其他空间或桶内其他前缀的内容 | 新增：backend/yuxi/modules/agents/runtime/sandbox/space_knowledge.py | `pytest backend/test/unit/agents/test_space_knowledge_backend.py -k "path_traversal_fails_before_touching_storage or glob_and_content_search_stay_inside_the_space or content_search_honours_glob_and_max_count or composite_remaps_paths_under_spaces"`（后者从复合后端发起 `/spaces/../spaces/index.md`，证明越界在真实路由下也被拒） | `read("/spaces/../../other-space/a.md")` 或绝对键能读到别的前缀 | Passed |
| 空间知识只读：写入与编辑被拒绝且对象不变 | 空间内容可被子 Agent 篡改 | 新增：backend/yuxi/modules/agents/runtime/sandbox/space_knowledge.py | `pytest backend/test/unit/agents/test_space_knowledge_backend.py -k writes_are_refused_without_touching_storage`（含同步与异步的 `write`/`edit`/`delete`/`upload_files`/`download_files`，并断言没有任何对象读取） | 写入返回成功但对象未变，或对象被改 | Passed |
| 路径不重复前缀：模型看到 `/spaces/<相对路径>` | 多拼一层 `/spaces/spaces/...` | 新增：backend/yuxi/modules/agents/runtime/sandbox/space_knowledge.py | `pytest backend/test/unit/agents/test_space_knowledge_backend.py -k composite_remaps_paths_under_spaces` 与 `test_space_knowledge_s3.py -k composite_exposes_space_under_its_mount_point` | `ls` 返回的 `path` 以 `/spaces` 开头，或返回 dataclass 导致 `fi["path"]` 报错 | Passed |
| 前缀无对象或桶尚未创建时，空间按空处理，根级 `glob`/`grep` 仍返回工作目录结果 | 一次对象存储失败把工作目录的根级检索结果整体打成失败 | 新增：backend/yuxi/modules/agents/runtime/sandbox/space_knowledge.py | `pytest backend/test/unit/agents/test_space_knowledge_backend.py -k "missing_bucket_keeps_root_glob_and_grep_working or agent_without_space_prefix_sees_empty_directory"`（用真实 `CompositeBackend`；`glob` 走根级合并，`grep` 分别以 `path="/"` 与 `path="/spaces"` 打到空间路由） | 桶未建时 `glob("**/*.md")` 返回 error 并丢掉 `/home/user/workspace` 结果 | Passed |
| `space_slug` 为空时不挂载；非空时挂载且前缀即该值 | 未绑定空间的 Agent 出现 `/spaces`，或已绑定却拿不到目录 | backend/yuxi/modules/agents/runtime/sandbox/backend.py | `pytest backend/test/unit/agents/test_space_knowledge_backend.py -k "composite_remaps_paths_under_spaces or agent_without_space_slug_mounts_nothing or agent_slug_alone_does_not_mount_a_space"`（`mounted` fixture 走真实 `create_agent_composite_backend`，只替换默认后端；后两条断言 `routes == {}`） | 未绑定的 Agent `routes` 非空；或仅凭 `agent_slug` 就挂载 | Passed |
| 配置表单的「业务空间」只列出已发布前缀 | 管理员对着空名单不能绑定，或绑定到不存在的空间 | backend/yuxi/modules/agents/runtime/base.py（选项注入） | 真实 HTTP：`GET /api/agent/{slug}` 的 `configurable_items.space_slug.options` 返已发布三个空间与篇数；`PUT /api/agent/{slug}` 写入 `config_json.context.space_slug` 后再读一致 | 选项为空，或保存后值丢失 | Passed |
| 空间内容不进任何向量库（构造上不存在索引入口） | 空间内容被索引进 Milvus | backend/yuxi/modules/knowledge/services/background_jobs.py(唯一入库入口) | `rg -n -e space_slug -e space_knowledge backend/yuxi/modules/knowledge` 无输出(已执行);`space_knowledge.py` 只 import `yuxi.infrastructure.minio` 与 `yuxi.modules.workspace.runtime_files.MAX_FILE_BYTES` | 空间前缀下的对象出现在知识库列表或出现 chunk 记录 | Inspected |
| 发布入口只对管理员开放，普通用户不能写空间前缀 | 任何登录用户都能改写空间知识 | backend/yuxi/api/routers/spaces.py（依赖注入 `get_admin_user`） | `docker compose exec api uv run --no-sync --group test pytest test/integration/api/test_spaces_router.py -k standard_user_is_refused`（真实 HTTP，普通用户 POST/GET/DELETE 均返回 403）；三条用例在容器内全过 | 普通用户 `POST /api/spaces/{slug}/files` 返回 200 | Passed |
| 发布侧拒绝越界路径与非 Markdown，且一条非法整批不写 | 借发布接口把任意对象写进桶，或半个空间被发布 | backend/yuxi/modules/agents/services/spaces.py | `pytest backend/test/unit/agents/test_space_publishing.py -k "publish_path or rejects_the_whole_batch"` 与容器内 `test/integration/api/test_spaces_router.py -k non_markdown_and_traversal_are_refused_over_http`，两者均实跑通过 | `paths=["../evil.md"]` 或 `.txt` 写入成功 | Passed |
| 首次发布自动创建 `spaces` 桶，删除只影响本空间前缀 | 发布需要人工先建桶；删除波及其它空间 | backend/yuxi/infrastructure/minio/client.py（`ensure_bucket_exists` 在 `upload_file` 内） | `pytest backend/test/unit/agents/test_space_publishing.py -k "creates_the_space_prefix or delete_space_only"`（已执行） | 删除空间 A 后空间 B 的对象也消失 | Passed |
| 新增空间只需一条 Agent 记录 + 一次前缀写入，无需改码 | 平台主干随空间数量增长而膨胀 | backend/yuxi/modules/agents/runtime/sandbox/backend.py（挂载点） | 本轮实跑：`rg -n "\\bspace" --type py backend/yuxi/modules/agents` 只命中 `backend/yuxi/modules/agents/services/artifacts.py` 的既有文案、挂载点 `backend/yuxi/modules/agents/runtime/sandbox/backend.py:32,117` 与新增模块自身；`rg -n -e space_scope -e space_id -e is_space --type py backend/yuxi/modules/agents` 无输出；除新增模块外 `space_slug` 只出现在 `backend.py:117` 一行；行为用例“加一条记录 + 写前缀即得新目录”由 `test_agent_without_space_prefix_sees_empty_directory` 与 `test_space_tree_lists_markdown_only` 间接证明 | 接入第二个空间后 `agents` 模块出现按空间标识的条件分支 | Inspected |

## 风险

- 空间内容没有版本与审批流程：任何能写对象存储前缀的人都能改空间知识；只在对象存储侧有版本化能力。若需要审批流，属于发布平台需求，不在本方案内。
- 前缀与 Agent 绑定靠配置项维护：绑定值存在 `config_json` 里，前缀改名、误删或从未发布都会导致目录变空；解绑只需清空「业务空间」选项。代码不做对账，也没有警告。
- 对象存储的 S3 层故障（字错桶名、删除了 `spaces` 桶、权限不足）与"空间本来为空"在模型侧不可区分：两者都降级为空目录，只在服务日志留一条 `列举空间前缀失败` 的 warning（`NoSuchBucket` 不记）。连接层故障（端点不可达、凭据错误）不被 `StorageError` 包裹，会向上传播并使根级 `glob`/`grep` 合并失败——这是有意的：这类故障通常已使工作区写入一起失败，不应当成"没有知识"静默处理。
- `ls` 每次调用把该子树的对象键全量拉回内存；单个空间内容树很大（数万键）时变慢。空间内容规模由开发者控制，超出时需改为按分隔符逐层列举。
- `grep` 每次把检索范围内全部 Markdown 下载进内存再匹配（未按 `max_count` 提前停止下载）；`CompositeBackend` 在 `path` 为 `/` 时也会带上 `/spaces` 路由，所以工作区根级检索会额外付一次空间全量下载。空间膨胀到上千文件、几十 MB 时应改为发布期前缀索引或按 `max_count` 早停。
- `spaces` 桶只收 `.md`，`read` 对非 Markdown 与非 UTF-8 返回错误；若业务资料有大量图片/表格，转换成本落在构建方，仓库既不提供转换工具，也没有上传时拦截——写错后缀只会在读取时暴露。
- 同一 Project 的子 Agent 共享 workdir，写入是覆盖语义，没有并发保护；`outputs/{space_slug}/` 只是提示词约定，模型不遵守时仍会互相覆盖，且覆盖无声（无版本、无冲突提示）。
- 授权模型依赖 `users.department_id`，一个用户只属于一个部门；跨部门成员或兼职场景需要另行建模。
- 没有空间内容的 Agent 也会看到空 `/spaces` 目录，消耗一点提示词预算；目录用途写在空间包的 `AGENT.md`（发布者编写，平台不注入），否则模型可能反复探测。
- 空间与 Agent 记录耦合：同一空间若要配多个角色 Agent，需要再引入一层承载方式；当前按"一个空间一个 Agent 记录"推进。
- `/spaces` 不在 runtime 虚拟根集合内，预览与产物路径能力对它不生效；若将来要求"把空间文档当工作目录文件处理"，需要同时改 `backend/yuxi/modules/workspace/runtime_paths.py` 与权限校验，不能只加常量。
