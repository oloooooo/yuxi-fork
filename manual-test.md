# 业务空间（Skill 方案）前端手动测试

适用：`mine/feat/space-skill-mount` 分支。该方案不再有 `/spaces` 页面和虚拟目录；空间由一个共享 Skill 包与一个同 slug 的共享 Agent 组成。

## 1. 准备环境

```bash
cd /path/to/Yuxi

# 宿主机 5432 被占用时改用 5433
YUXI_POSTGRES_PORT=5433 docker compose --profile container up -d --wait \
  api worker sandbox-runner graph frontend

curl -s -o /dev/null -w '%{http_code}\n' http://localhost:5050/api/system/ready
```

- 期望 `/api/system/ready` 返回 `200`，`api`、`worker`、`sandbox-runner`、`graph`、`frontend` 均 healthy。
- 打开 `http://localhost:5173`。
- 准备一个管理员账号（上传 Skill、创建 Agent）和两个普通用户：一个与 Skill/Agent 同部门，另一个在其他部门。

## 2. 准备测试用 Skill 包

在 Git Bash 或 Linux 中执行：

```bash
rm -rf /tmp/hybrid-bonding-skill
mkdir -p /tmp/hybrid-bonding-skill/references

cat > /tmp/hybrid-bonding-skill/SKILL.md <<'EOF'
---
slug: hybrid-bonding
name: 混合键合空间
description: 混合键合工艺知识空间；需要键合温度、工艺窗口与 DOE 资料时使用。
---

# 混合键合空间

先读 `references/index.md`，再按索引读取资料。只使用本 Skill 目录内容，回答注明来源文件。目录只读。
EOF

cat > /tmp/hybrid-bonding-skill/references/index.md <<'EOF'
# 资料索引

- [rules.md](rules.md) —— 键合温度与工艺窗口
EOF

cat > /tmp/hybrid-bonding-skill/references/rules.md <<'EOF'
# 键合规则

推荐键合温度：200–250 °C。

预键合阶段为 20–25 °C 常温；退火阶段按 200–250 °C 执行。
EOF

cd /tmp/hybrid-bonding-skill
zip -r ../hybrid-bonding.zip .
```

生成的 `/tmp/hybrid-bonding.zip` 根目录应包含 `SKILL.md` 和 `references/`。包内只能有一个 `SKILL.md`。

## 3. 管理员上传并授权 Skill

1. 管理员登录，进入「知识库 · 技能」→「技能」。
2. 点击「上传 Skill」，选择 `/tmp/hybrid-bonding.zip`。
3. 在安装确认页选择「共享 Skill」，读取范围选「部门共享」，勾选测试部门。
4. 确认安装。
5. 检查技能列表出现「混合键合空间」（slug `hybrid-bonding`），可见范围为该部门。

## 4. 创建空间子 Agent

1. 进入「智能体管理」→「新增智能体」。
2. 名称与标识使用 `hybrid-bonding`，共享范围设为与 Skill 相同的部门。
3. 进入「工具配置」：
   - 在 Skills 中只选「混合键合空间」；
   - 在「预加载 Skills」中选择该 Skill（或「选择全部」，当前只选了一个）。
4. 进入「模型配置」→「系统提示词」，填入：

   ```text
   你是混合键合工艺空间助手。知识只来自只读目录 /home/user/skills/hybrid-bonding/。
   回答前先读 /home/user/skills/hybrid-bonding/references/index.md，再按索引读取资料。
   只使用该目录内容，回答注明来源文件。目录只读，不要写入。
   ```

5. 保存 Agent。
6. 如 Agent 创建时共享范围不是部门，回到「基本信息」把它改为同一部门并保存。

## 5. 同部门普通用户验证

1. 退出管理员，用同部门普通用户登录。
2. 新建对话，在智能体选择器中选择 `hybrid-bonding`。
3. 提问：

   ```text
   推荐键合温度区间是多少？请给出 Skill 中的来源文件。
   ```

4. 期望结果：
   - 工具调用中出现 `Skill | hybrid-bonding`；
   - 读取 `/home/user/skills/hybrid-bonding/references/index.md` 和 `/home/user/skills/hybrid-bonding/references/rules.md`；
   - 回答包含 `200–250 °C`，并引用 `references/rules.md`；
   - 技能页能看到同名 Skill，但普通用户没有上传/编辑入口。

## 6. 负向与边界检查

- 其他部门用户登录后，智能体列表没有 `hybrid-bonding`，技能列表搜索 `hybrid-bonding` 也没有结果。
- `GET /api/spaces` 应返回 `404`（该方案已删除旧空间路由）。
- 只读检查：让 Agent「把 test 写入 `/home/user/skills/hybrid-bonding/test.md`」，应被拒绝或返回只读错误。
- 授权漂移（可选）：管理员把 Skill 的读取范围改到另一个部门，保留 Agent 的部门不变。同部门用户仍能看到 Agent，但重新运行时应出现“部分配置能力不可用”告警，且 `/home/user/skills/hybrid-bonding/` 不存在，Agent 无法完成空间任务。
- 删除或停用 Skill 后，下一次运行不应再挂载该目录。

## 7. 清理

1. 「智能体管理」→ 对应 Agent →「更多操作」→ 删除。
2. 「知识库 · 技能」→ 该 Skill → 删除/卸载。
3. 如创建了临时部门/用户，在「设置 → 部门管理」「用户管理」中删除。
4. 删除本地测试包：`rm -rf /tmp/hybrid-bonding-skill /tmp/hybrid-bonding.zip`。

## 8. 常见问题

- **上传后找不到 Skill**：确认 ZIP 根目录有 `SKILL.md`，frontmatter 的 `slug` 合法且没有重复。
- **Agent 下拉里没有该 Skill**：确认 Skill 的读取范围包含当前部门；共享 Skill 需要安装成功，刷新页面后再选。
- **普通用户看不到 Agent**：检查 Agent 的共享范围是否与 Skill 的部门一致；Agent 和 Skill 各自授权，运行时取交集，不会自动继承。
- **回答仍引用 `/spaces/...`**：提示词没有改成 `/home/user/skills/hybrid-bonding/`，或 Agent 仍挂着旧的空间配置。
- **回答没有调用 Skill**：检查是否在 Agent 的 Skills 中选中并预加载了该 Skill；预加载才会从首轮注入完整说明。
