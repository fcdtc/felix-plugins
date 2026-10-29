# 集中式适配

协调主仓维护开发文档、需求／工单、跨仓术语、架构决策和共享契约；服务子仓各自维护源码、构建输入及本服务契约。实际 IDL 归属从项目约定确认，集中式组织不决定 IDL 必须集中。

## 项目登记

优先读取已有登记。没有清单时只请求协调仓和明确的服务仓集合，不凭相邻目录自动纳管。新配置的最小形式如下，所有示例值必须替换为已核实信息：

```json
{
  "version": 1,
  "coordinator": {"name": "control"},
  "development_branch": "codex/dev",
  "projects": [
    {"name": "api", "path": "../api"},
    {"name": "worker", "path": "../worker"}
  ]
}
```

主仓名和子仓名有效且唯一，至少一个子仓；`path` 相对目标主仓或为明确绝对路径。初始化前查验每个路径是独立 Git 仓库根，保留已有元数据。`development_branch` 默认 `codex/dev`；其他分支需明确选择和平台绑定核实。

## 交付文件

- 六份公共手册与基础代理约定放在主仓。环境说明采用[集中式环境模板](templates/centralized/environment.md)，补充[工作区手册](templates/centralized/workspaces.md)及[项目归属](templates/centralized/projects.md)。
- 安装资产中的 `scripts/workspace`、`scripts/workspace.py`、`tests/test_workspace.py`，并使 wrapper 可执行。工具根按复制后的 `scripts/` 位置定位；不从技能安装目录执行业务 workspace 命令。
- `.gitignore` 增量加入 `/projects.local.json`；已有跟踪绑定先报告冲突，不静默删除。工作区创建工具要求主仓基线包含正式配置、AGENTS.md／CLAUDE.md 定位规则（支持仓内相对符号链接）、脚本与忽略规则，因此初始化后要由用户明确要求提交，再创建实际工作区。
- 初始化不生成 `projects.local.json`／`workspace.json`。这两类运行时绑定由后续真实 `create` 生成；项目登记不是当前 Session 的跨仓写入授权。

## 主仓与子仓入口

主仓代理入口：用户在 Session 前创建完整 workspace；Agent 先 `scripts/workspace check`，只从 `projects.local.json` 绑定的路径访问子仓，通过 `path/exec` 定位；绑定缺失、项目集或身份变化时停止跨仓写入，不回退正式配置的源目录。

子仓入口：读取当前工作区的同级协调仓文档与工单；集中式登记的名称对应实际 worktree 名。同级 `../<协调仓名>` 链接仅在此布局可验证时使用；默认检出不符时按真实相对路径处理，不能留下断链。修改子仓 README／代理入口前核对明确授权和文件内容；已有独立 tracker／领域规则冲突时先决定归属，不静默替换。

初始化工具只生成主仓产物；子仓入口由 Agent 按已确认清单及适用规则合并。不复制一套中央工单文档到子仓；跨仓任务的实施记录按项目区分 commit、制品与验收。

## 脚本策略

保留完整 `create/check/path/exec/remove`。工作区包含全部登记仓库，固定配置的开发分支；基线仅该分支的本地或 origin 引用。名称／分支／项目集变化需创建新的完整工作区，不原地补仓。

`create`、`remove` 是用户明确执行的工作区生命周期动作；开发 Agent 使用 `check/path/exec`。Git 分支不能同时占用多个 worktree，worktree 不隔离共享平台资源。路径校验不是文件系统沙箱。

资产是新初始化 schema，不承诺兼容旧 workspace manifest。已有脚本或运行时绑定时先比较，不能覆盖或执行迁移来解决冲突。
