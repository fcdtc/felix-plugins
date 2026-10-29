# 集中式开发环境

协调主仓是统一 Session 和文档入口，服务子仓独立维护代码与构建。项目归属见[项目登记](projects.md)，workspace 操作见[工作区手册](workspaces.md)。开发分支默认 `codex/dev`；实际以 `projects.json` 的 `development_branch` 为准。

## 开始开发

1. 读取主仓与目标子仓适用的代理规则、需求／工单及相关领域文档，按实际真源定位。
2. 从已绑定主仓执行 `./scripts/workspace check`，完整校验通过后使用 `path/exec` 定位目标仓。绑定缺失或变化时保留现场，交由用户协调；不按默认源路径直接跨仓写入。
3. 检查目标仓现有修改与语言、生成、构建、测试配置。保留原有工作，构建输入来自本仓和固定依赖，不依赖相邻 checkout。
4. 执行本次所需检查，各仓结果分别记录；生产初始化和生命周期语义保持。本地通过、远端部署和业务通过分别报告。

## Git 与 worktree

各仓独立提交；日常开发遵循配置的分支。commit／push 仅在明确要求时执行；push 前核实自动链路触发影响，见[后端构建与发布](../development/backend-service-release.md)。分支存在不证明平台绑定。

workspace 创建／删除由用户明确执行，Agent 在开发任务中只检查和使用。工作区隔离文件，不隔离数据库、部署或 MIA 资源；共享平台操作先读[平台操作](../development/platform-operations.md)。
