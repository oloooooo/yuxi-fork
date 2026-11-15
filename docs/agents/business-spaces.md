# 业务空间

业务空间是一组按部门共享的领域资料，以及使用这些资料的子 Agent。平台不提供独立的“空间”对象或 `/spaces` 虚拟目录；一个空间由两件现有资源组成：

- 一个共享 Skill 包：保存空间知识与操作说明，按部门授权，运行时目录只读；
- 一个共享 Agent：作为该空间的子 Agent，按同一部门授权，选择并预加载对应的 Skill。

Skill 与 Agent 使用同一个 slug，例如 `hybrid-bonding`。用户在对话中看到子 Agent，在 Skill 列表中看到同名空间 Skill；运行子 Agent 时，Skill 已预加载，资料以只读文件挂载在 Skill 路径下。

## 配置一个业务空间

### 1. 制作空间 Skill 包

```text
hybrid-bonding/
├── SKILL.md
└── references/
    ├── index.md
    ├── bonding-window.md
    └── 键合工艺DOE.md
```

`SKILL.md` 至少包含 `name`、`description` 和稳定的 `slug`；正文写明领域边界、先读哪个索引、引用格式和只读约束。`references/` 存放领域资料。标准包结构、依赖字段和大小限制见 [管理 Skills](./skills-management.md#创建-skill)。

### 2. 上传并设置部门可见性

按[管理 Skills](./skills-management.md#创建-skill)上传 ZIP 或单个 `SKILL.md`，在确认草稿时选择“共享”，并把读取范围设为需要该空间的部门。

### 3. 创建子 Agent

新建共享 Agent，slug 与 Skill 相同，并配置：

- **Skills**：选中该空间 Skill；
- **预加载**：选中同一 Skill，使子 Agent 从首轮获得完整说明；
- **共享范围**：与 Skill 相同的部门；
- **系统提示词**：定义角色、回答边界和引用要求。

子 Agent 的 Skill 路径是 `/home/user/skills/<slug>/`。提示词中引用资料时使用该路径，不要再写 `/spaces/...`。例如：

```markdown
你是 hybrid-bonding 空间的工艺专家。先读
`/home/user/skills/hybrid-bonding/references/index.md`，再按问题读取对应专项文件；回答必须给出来源文件。
```

## 用户看到什么

部门成员登录后：

- 在智能体列表看到该空间的子 Agent；
- 在 Skill 列表看到同名 Skill，并可查看说明与文件；
- 启动子 Agent 时，运行时按当前用户权限解析 Skill，预加载 `SKILL.md`，并把整个 Skill 目录只读投影到 `/home/user/skills/<slug>/`；
- 子 Agent 读取 `references/` 时，看到的是当前授权内容；写入被拒绝。

不需要为每个用户单独分配 Skill，也不需要在 Agent 上逐个添加成员。部门授权同时决定 Agent 与 Skill 的可见性。

## 权限与一致性

Agent 和 Skill 各自保存共享范围，运行时取交集：

- 用户不在 Agent 的部门范围：看不到、也不能运行该子 Agent。
- 用户不在 Skill 的部门范围：Agent 仍可能存在，但运行时 Skill 被过滤，并给出“部分配置能力不可用”告警；提示词中的 `/home/user/skills/<slug>/` 不存在，子 Agent 无法完成空间任务。
- Skill 被停用、删除或移出部门：下一次运行刷新投影后不再挂载。

因此新增或调整空间时，必须同时更新 Agent 与 Skill 的部门范围。当前没有跨资源的一致性校验，也不校验同 slug 的 Agent 与 Skill 是否成对存在；这是有意的运行时交集语义，不是自动继承。

## 更新空间内容

在 Skill 详情页编辑文件或上传新版本，不需要改 Agent。共享 Skill 的 `SKILL.md` 和预加载正文在每个模型边界重新读取；运行中修改已注入的 Skill 内容会使当前 Run 以“已注入的 Skill 权限或来源已改变”失败，需要重新发起运行。

## 迁移已有 `/spaces` 内容

如果已有旧实现发布的空间内容，按以下步骤迁移；旧桶数据需要手动清理。

1. 把原空间目录整理成 Skill 包：`AGENT.md` 的角色说明移入 Agent 系统提示词，知识文件放入 `references/`，并新增 `SKILL.md` 索引。
2. 用原空间 slug 作为 Skill 与 Agent 的 slug。
3. 把提示词和工具说明中的 `/spaces/...` 改为 `/home/user/skills/<slug>/...`。
4. 按部门重新配置 Skill 与 Agent 的共享范围。

部署前结束或重跑仍带 `space_slug` 的进行中 Run；新版本会忽略该字段，旧空间目录不会挂载。

## 限制

- 共享 Skill 源目录由 `YUXI_SKILL_DATA_DIR` 指定，投影目录由 `YUXI_SKILL_PROJECTION_DIR` 指定，沙盒节点以 `CUBE_NODE_SKILL_PROJECTION_DIR` 只读挂载投影根。当前部署是共享卷而非对象存储。多主机部署必须让所有 API/Worker 与沙盒节点挂载同一源卷/投影卷；否则各实例看到的 Skill 内容可能不一致。
- Skill 包允许脚本和任意参考文件，容量上限为 50 MiB、2000 个条目，上传 ZIP 上限 10 MiB。旧空间只接受 Markdown；迁移时需检查包内容。
- Skill 投影是“同一用户可访问内容的只读副本”，不是用户之间的文件保密边界。能访问同一 Skill 的用户看到相同内容。
