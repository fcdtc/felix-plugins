# 集中式工作区

用户在开启开发 Session 前创建全部登记仓库的 worktree，再在输出的协调主仓路径开启 Session。Agent 使用已绑定路径，不自动创建、补仓、切分支或删除工作区。

## 创建前置条件

Python 3.9+、Git 2.31+，macOS／Linux，使用 Python 标准库。项目名、源路径由 `projects.json` 登记，开发分支由 `development_branch` 指定，缺省为 `codex/dev`。

先检查源仓身份、分支占用与引用。主仓基线须已提交正式配置、`AGENTS.md` 或 `CLAUDE.md` 定位规则（支持仓内相对符号链接）、`scripts/workspace`、`scripts/workspace.py` 和 `/projects.local.json` 忽略规则；工具不会提交、fetch 或 push。缺引用时先由用户明确安排获取。

```sh
./scripts/workspace create task01
```

默认工作区集合在主检出父目录的 `.workspaces/`，可用 `--root /明确路径` 指定其他位置；该位置须在已有 checkout 之外。全仓使用配置分支，同一分支不能同时占用多个 worktree。逐仓 `--base 项目=引用` 仅允许配置分支的本地或 origin 引用；不从其他分支创建。已占用分支先协调，不 reset 或强制抢占。

创建固定每仓身份、起始 commit 和绝对路径，生成 `workspace.json` 及协调主仓的 `projects.local.json`。仅完整校验通过才输出就绪。

## 开发时使用

```sh
./scripts/workspace check
./scripts/workspace path <项目名>
./scripts/workspace exec <项目名> -- <目标仓命令>
```

从默认检出选择已有工作区：

```sh
./scripts/workspace check task01
./scripts/workspace path --workspace task01 <项目名>
./scripts/workspace exec --workspace task01 <项目名> -- git status --short
```

自定义集合目录时加 `--root`；`exec` 选项置于项目名之前。检查所有绑定仓库的身份、分支、基线继承、项目集合及配置策略；通过后才能使用路径。没有绑定或绑定失效时停止跨仓写入，正式项目配置只用于创建时定位源仓，不是运行时回退路径。

## 重复调用与恢复

相同 `create` 校验并复用，保留已有修改和新提交。部分失败保留清单与已创建 worktree，排除原因后重跑原命令。工作区名、项目集合、分支策略及基线不能通过重复调用偷偷变更；需收起旧工作区并按新配置创建完整工作区。

绝对路径绑定不支持静默搬迁。新增项目后不原地补仓。`check/exec` 是定位门禁，不是文件系统沙箱；命令显式访问其他路径仍受工具权限约束。

## 删除与备份

```sh
./scripts/workspace remove task01
```

用户执行前逐仓检查未提交工作、相对基线新增的提交及交付状态。删除拒绝未提交／未跟踪文件，先备份 Git 忽略文件和工作区清单外内容至集合目录的 `.workspace-archive/`，再移除 worktree；本地分支及其提交保留。失败或部分移除先检查 Git 状态和备份，不重复强制删除未知目录。

## 本地验证

```sh
python3 -m unittest discover -s tests -p 'test_workspace.py' -v
```

测试只使用临时 Git 仓库。测试通过证明脚本行为，不证明真实平台链路已就绪。数据库、环境和 MIA 资源须另行协调。
