# MIA RPC 接入发布链

将已部署后端 RPC 接入 MIA：BAM → Overpass → MIA RPC Version／schema snapshot → Workflow → Tool → Host → 业务验收。

先按[后端构建与发布](backend-service-release.md)验证目标后端，再读[平台操作](platform-operations.md)。Host 的专用／增量更新规则以[Agent 受管发布](mia-agent-release.md)为准，本页只补充 RPC 全链依赖与门禁。

## 进入条件与阶段门禁

实施目标须明确：权威 IDL、后端 PSM／方法与部署版本、MIA 管理上下文和数据面环境、Host 模式、验收 case、写入授权及恢复边界。当前本项目尚未确立这些资源，不沿用其他服务的配置或发布 ID。

每阶段进入下一步前具备：成功终态、针对该终态的一致回读、本次实施证据。草稿保存是迭代完成点，不是发布完成点；请求提交、debug 成功或页面提示不能替代发布态回读。

先加载当前 BAM／Overpass 和 `miacli` 对应分支，查看目标 `--help`。不固化历史 Human-only 能力矩阵；CLI 确实不支持的 MIA 动作按[人工交接](platform-operations.md#人工交接)处理，准备交接时读取[具体场景、依赖批次与模板](human-handoff.md)。

## 1. BAM：权威 IDL 版本

- 输入：IDL 仓／文件／分支／精确 commit、目标 PSM／方法、兼容性结论及已部署后端版本。
- 操作：查询既有接口与版本，按当前官方能力检测、同步或发布目标 IDL。
- 完成标准：版本来源与目标 commit 对应；方法及 Request／Response 与权威 IDL 一致，兼容检查通过并已回读。
- 证据：IDL commit、BAM 分支／版本／任务入口、目标方法、兼容结果和终态。

版本不一致、方法缺失或检测失败时停在本层，修复权威源或配置后恢复原任务，不以旧成功版本为新 commit 背书。

## 2. Overpass：分支 RPC 产物

- 输入：包含上一步 IDL commit 的已推送远端分支、目标 PSM／方法和生成参数。
- 操作：通过官方 CLI 同步分支 IDL、回读、生成并跟进任务；命令以当前帮助为准。
- 完成标准：生成成功，产物包含目标方法；用 commit、IDL 内容或 schema hash 证明与 BAM 对应。Overpass 读取远端分支，不直接把 BAM version 当作其输入。
- 证据：远端分支、IDL commit、生成任务终态、平台产物标识／摘要、方法清单及 BAM 对应关系。

生成失败、来源不一致或结果不明时停止下游，优先恢复原任务，不用本地未提交文件或历史产物替代。

## 3. MIA RPC Version：固定 schema

- 输入：已验证 Overpass 产物、PSM／方法、管理上下文和数据面环境。
- 操作：按当前 CLI 支持范围查询或管理 RPC Version／snapshot；缺少创建能力时由获授权人员在平台正式入口完成。
- 完成标准：固定版本／snapshot 可回读，来源对应目标产物，方法及请求响应 schema 完整，目标 Workflow 可选择。
- 证据：RPC Version／snapshot、Overpass 对应关系、schema 摘要、环境和回读结果。

方法不可选或仍引用旧 schema 时先修正来源，不继续发布 Workflow；保留失败状态，不猜 ID 或覆盖其他版本。

## 4. Workflow：固定编排版本

- 输入：固定 RPC snapshot、预期图结构、输入／输出映射、错误出口、超时与重试边界。
- 操作：按当前 Workflow 技能读取指定基线、修改完整 nodes／edges、保存 WIP 并回读。schema 变化需要重选 RPC Version／方法生成 snapshot 时，先核实 CLI 能力；平台重置请求模板后恢复映射。获准 debug 后检查真实输入、输出与错误，再按明确发布目标使用支持的入口发布。
- 完成标准：已发布固定版本可回读；snapshot、节点及变量映射符合契约。debug 成功或 WIP 保存不替代发布回读。
- 证据：motif、基线／草稿／发布 graph、乐观锁值、Workflow 固定版本、snapshot、定义摘要和诊断结果。

保存冲突先回读合并；schema 或映射漂移先在本层恢复，不更新 Tool。Start 入参调整可能影响顶层映射、数据集和评测，按当前技能检查并明确未覆盖项。

## 5. Tool：固定 Workflow 绑定

- 输入：已发布 Workflow 固定版本、Tool 名称／描述、严格输入 schema、输出及错误语义、权限／风险／超时约定。
- 操作：用当前 Component 能力回读；CLI 无创建、编辑或发布入口时批次交接。切换 Workflow 后重新核对完整输入 schema，避免平台生成模板扩大参数边界。
- 完成标准：Tool 为固定正整数发布版本，schema 与契约一致，绑定目标 Workflow 固定版本而非草稿／浮动最新版。
- 证据：Component／revision／固定版本、Workflow 绑定、schema 摘要与回读结果。

只有草稿、绑定漂移或回读不一致时停止 Host 更新；恢复后从已有 Tool 版本／草稿继续。

## 6. Host：发布固定资源全集

- 输入：已发布固定 Tool，必要时固定 Skill；稳定路由、Host 模式及验收环境。
- 操作：执行[专用 Agent 或现有 Host 增量链](mia-agent-release.md)。
- 完成标准：最终发布态可回读，目标依赖固定；现有 Host 全部非目标资源保持，父子版本可追溯。
- 证据：路由、motif／Agent、草稿锁值、父及最终发布版本、固定资源全集、非目标前后对比。

资源丢失、冲突或回读不明时停止验收，按 Agent 手册恢复。需要更换依赖时重新验证该层及后续发布态。

## 7. 最终业务验收

从最终发布 Host 发起获准的真实 Session／run，回读 Tool／Workflow 轨迹，并关联后端部署及日志。API 测试、直接 RPC 和 Workflow debug 可辅助定位，但不能替代 Host 全链验收。

完成标准：批准的正例、反例和身份边界符合契约；同一证据可追溯到固定 Host、Tool、Workflow、RPC snapshot、BAM／Overpass 及实际后端运行版本。成功码或 `RUN_FINISHED` 不单独代表业务通过。

以真实 Tool 请求／响应及权威业务状态判定，不以 Host 自然语言回复替代。LogID 和 trace 分开记录；Header 测试与身份边界按[Agent 验收约定](mia-agent-release.md#运行与验收)核实，不迁移其他项目的身份字段、fallback 或数据隔离规则。

## 恢复与最终证据

定位最早失败层：契约问题从 BAM／Overpass 恢复，映射问题从 Workflow 恢复，绑定与路由问题从 Tool／Host 恢复，业务实现问题从后端构建部署恢复。结果不明时查原任务；每次新版本重新验证受影响层及其下游，不用旧 Session 或其他制品证明新版本。

工单关闭前形成完整可追溯关系：IDL commit、BAM、Overpass、RPC snapshot、Workflow、Tool、Host、Session／run／日志、后端 artifact／运行版本和业务断言。同时列出非目标资源对比、失败任务终态、已覆盖／未覆盖 case 及回滚入口。仅最终全链业务验收通过才声明 MIA RPC 接入发布完成。
