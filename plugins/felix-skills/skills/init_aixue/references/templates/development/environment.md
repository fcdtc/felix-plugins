# 开发环境与分支

本仓库是独立开发入口：需求、工单、领域文档和源码按本仓约定维护。无需依赖相邻项目检出、多仓绑定文件或工作区创建脚本。

## 开始开发

1. 核对实际操作目录、仓库身份、分支和已有修改：

   ```sh
   git rev-parse --show-toplevel
   git remote get-url origin
   git branch --show-current
   git status --short
   ```

   完成标准：确认当前目录是目标仓库，识别并保留已有修改，当前开发分支为 `codex/dev`。
2. 读取目标路径适用的 `AGENTS.md`，按[领域约定](../agents/domain.md)读取术语和相关 ADR；有指定需求或工单时按[任务跟踪约定](../agents/issue-tracker.md)读取。
3. 从仓内配置识别语言版本、依赖、生成、构建和测试入口。当前初始 harness 不规定 Go module、RPC 框架或工具链版本；服务方案确认后以实际配置为准。缺少可执行入口时记录缺口，不使用相邻仓库的脚本代替。
4. 按变更范围执行格式化、静态检查、最小测试和构建。完成标准：报告实际命令、结果及未执行范围；依赖或网络阻塞与源码失败分开记录。

## Git 与 worktree

- 日常实现留在 `codex/dev`，不为普通任务另建开发分支。
- 当前分支不符时先回读工作树及 worktree 占用，确认切换安排；保留已有修改，不自动 reset、stash 或覆盖文件。
- 用户要求使用 worktree 时先运行 `git worktree list`，检查 `codex/dev` 是否已被占用。Git 不能在两个 worktree 同时检出该分支；交由用户协调占用，不通过改成其他分支规避部署约定。
- worktree 只隔离本地文件，不隔离平台环境、数据库或 MIA 资源；共享资源写入遵循[平台操作](platform-operations.md)。
- commit 和 push 仅在用户明确要求时执行；push 前确认范围、精确 commit 和平台触发影响。平台收到 push 后自动部署，不代表 Agent 获得自动 push 授权。
- `codex/dev` 的存在不证明自动触发已绑定；核实和跟进流程见[后端构建与发布](backend-service-release.md)。

## 工程与交付边界

构建输入须来自本仓及显式固定的依赖，不能依赖相邻 checkout。IDL／契约采用唯一权威来源，实施前确认位置与生成命令；不手改生成物代替修正源定义。

只修改需求范围内的实现；本地验证保留生产初始化、生命周期和依赖语义。业务测试通过、本地构建通过和远端发布通过分别给出结论。
