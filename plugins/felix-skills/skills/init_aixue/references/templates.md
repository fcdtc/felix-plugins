# 模板与适配

模板的稳定操作流程作为技能内部真源维护；生成后目标仓库拥有自己的副本，不依赖技能路径。模板不是目标资源配置或已部署证据。

| 产物 | 内部模板 | 适配重点 |
| --- | --- | --- |
| 开发环境 | [独立式](templates/development/environment.md)／[集中式](templates/centralized/environment.md) | 仓库形式、实际分支、工具链与工作区入口 |
| 平台操作 | [platform-operations.md](templates/development/platform-operations.md) | 当前工具、授权与证据真源 |
| 后端发布 | [backend-service-release.md](templates/development/backend-service-release.md) | 已有服务／构建状态、实际分支和自动绑定 |
| MIA Agent | [mia-agent-release.md](templates/development/mia-agent-release.md) | 已有模板、资产和稳定路由，未知项待确认 |
| RPC 接入 | [mia-rpc-tool-host-release.md](templates/development/mia-rpc-tool-host-release.md) | 契约、PSM、版本与阶段门禁 |
| 人工交接 | [human-handoff.md](templates/development/human-handoff.md) | 保留七类场景、模板及回读标准，运行时填真实值 |
| 基础代理约定 | [工单](templates/agents/issue-tracker.md)、[分诊](templates/agents/triage-labels.md)、[领域](templates/agents/domain.md) | 缺失时补齐，已有真源优先 |
| 集中式扩展 | [workspaces.md](templates/centralized/workspaces.md)、[projects.md](templates/centralized/projects.md) | 已确认项目集合、路径与 workspace 使用 |

## 适配约束

1. 本次流程模板源于独立初始项目，诸如“尚无代码／模板／绑定”的断言，必须与目标仓和平台核查结果匹配；已有能力改成定位其实际入口，不能宣布不存在。
2. 当前 CLI 技能和帮助决定能力，保持人工交接的条件性判定；不固化本机安装路径、包版本或旧 Human-only 限制。
3. 稳定项目配置确定后写入目标真源；动态发布标识只写实施证据。文档可保留交接模板占位符，但不能在可执行项目配置中保留伪造路径或资源。
4. 不迁移来源仓名、SPA 服务职责、固定路由、资源 ID、共享数据库及身份字段；生成文档链接使用目标仓的相对路径。
5. README／代理入口由初始化工具提供缺失文件草稿；现有真源增量合并，不追加重复导航。基础代理文件若保留自定义内容，相关入口也指向其实际位置。
6. 项目分支改变时所有流程文本同步适配，但不推导 SCM／BITS 已绑定。集中式配置和脚本统一使用 `development_branch`。
