# 业务空间（`/spaces`）前端手动测试

适用：`main` 分支的 `/spaces` 只读 VFS 方案。测试目标：管理员发布空间内容后，Agent 能通过 `/spaces` 只读访问，普通用户按 Agent 的共享范围使用。

## 1. 准备环境

```bash
cd /path/to/Yuxi

# 如果宿主机 5432 已被本地 PostgreSQL 占用，改用 5433
YUXI_POSTGRES_PORT=5433 docker compose --profile container up -d --wait \
  api worker sandbox-runner graph frontend

curl -s -o /dev/null -w '%{http_code}\n' http://localhost:5050/api/system/ready
```

- 期望：`/api/system/ready` 返回 `200`；`api`、`worker`、`sandbox-runner`、`graph`、`frontend` 均为 healthy。
- 打开 `http://localhost:5173`。
- 需要两个账号：一个管理员（发布空间、编辑 Agent），一个普通用户（验证实际对话）。普通用户应与 Agent 在同一个共享范围（全局或同一部门）。

## 2. 管理员发布空间

1. 用管理员登录，左侧应出现「业务空间」。
2. 进入「业务空间」，在「空间标识」填 `hybrid-bonding`。
3. 点击「选择文件夹」，选择仓库中的：

   ```text
   backend/test/data/spaces/hybrid-bonding/
   ```

   目录下应载入 7 个 Markdown 文件，包括 `index.md`、`bonding-window.md`、`anneal-and-characterization.md`、`键合工艺DOE.md` 等。
4. 点击「发布 7 个文件」。
5. 检查：
   - 「选择空间」出现 `hybrid-bonding 7`；
   - 「已发布文件」列出 7 个文件；
   - 再次发布同名文件会覆盖，不会产生重复条目。

说明：只接受 Markdown；文件夹内的子目录会保留为相对路径；空间标识只允许小写字母、数字、`-`、`_`。

## 3. 配置空间 Agent

1. 进入「智能体管理」→「新增智能体」，或编辑一个已有 Agent。
2. 基本信息中设置共享范围。只做本测试时可设为全局共享；要验证部门隔离则设为部门共享并选择部门。
3. 名称/标识建议与空间标识一致，例如 `hybrid-bonding`。若已有同名 Agent，直接编辑它或先删除。
4. 进入「工具配置」：
   - 若只验证 `/spaces`，把 Skills 和预加载 Skills 清空，避免同时从 Skill 目录读取内容。
5. 进入「其他配置」→「业务空间」，选择 `hybrid-bonding（7 篇）`。
6. 进入「模型配置」→「系统提示词」，填入类似：

   ```text
   你是混合键合工艺空间助手。知识只来自只读目录 /spaces/。
   回答前先读 /spaces/index.md，按索引读取资料。
   只使用该目录内容，回答注明来源文件。目录只读，不要写入。
   ```

7. 保存 Agent。

## 4. 普通用户验证

1. 退出管理员，用同一共享范围内的普通用户登录。
2. 新建对话，在智能体选择器中选择刚配置的 `hybrid-bonding`。
3. 提问：

   ```text
   推荐键合温度区间是多少？请给出 /spaces 中的来源文件。
   ```

4. 期望结果：
   - 工具调用中出现读取 `/spaces/index.md`、`/spaces/bonding-window.md`、`/spaces/anneal-and-characterization.md`、`/spaces/键合工艺DOE.md` 等；
   - 回答给出退火 `250–300 °C`、预键合 `20–25 °C`；
   - 回答引用 `/spaces/...` 文件路径，而不是 `/home/user/skills/...`。

## 5. 负向与边界检查

- 普通用户侧不应出现「业务空间」导航；直接访问 `/spaces` 页面应被拒绝或跳转。
- 普通用户请求 `GET /api/spaces` 应返回 `403`（该接口仅管理员可用）。
- 若 Agent 设为部门共享，其他部门的用户应看不到该 Agent，也不能通过 URL 直接打开。
- 只读检查：让 Agent「把 test 写入 `/spaces/test.md`」，应被拒绝或返回只读错误；刷新「已发布文件」不应出现 `test.md`。
- 在「业务空间」页删除整个空间后，Agent 再回答时应读不到 `/spaces` 内容（或明确报错）。

## 6. 清理

1. 「智能体管理」→ 对应 Agent →「更多操作」→ 删除。
2. 「业务空间」→ 载入 `hybrid-bonding` →「删除整个空间」。
3. 如创建了临时部门/用户，在「设置 → 部门管理」「用户管理」中删除。

## 7. 常见问题

- **页面打不开/接口 502**：确认 `api` 与 `worker` 都在运行，且 `/api/system/ready` 为 200；只启动 `api` 不够，worker 还依赖 `graph`。
- **5432 端口冲突**：宿主机本地 PostgreSQL 占用了端口，使用上面的 `YUXI_POSTGRES_PORT=5433`。
- **Agent 的「业务空间」下拉为空**：先确认空间已发布；刷新 Agent 编辑页；空间选项来自已发布空间列表。
- **普通用户看不到 Agent**：检查 Agent 的共享范围是否包含该用户所在部门。
- **回答没有引用 `/spaces`**：检查系统提示词是否明确要求先读 `/spaces/index.md`，以及是否误选了 Skill。
