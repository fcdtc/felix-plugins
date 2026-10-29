# 任务跟踪：本地 Markdown

本仓库的需求和工单以 Markdown 文件保存在 `.scratch/`。

## 文件约定

- 每个功能使用独立目录：`.scratch/<feature-slug>/`。
- 需求说明保存为 `.scratch/<feature-slug>/spec.md`。
- 实施工单各占一个文件：`.scratch/<feature-slug>/issues/<NN>-<slug>.md`，
  从 `01` 开始编号，不合并为单个工单汇总文件。
- 工单文件顶部附近使用 `Status:` 行记录分诊状态；
  角色字符串见 `triage-labels.md`。
- 评论和讨论历史追加到文件末尾的 `## Comments` 标题下。

## 发布与读取

技能要求“发布到任务跟踪系统”时，在 `.scratch/<feature-slug>/`
下创建对应文件，按需创建目录。

技能要求“读取相关工单”时，读取指定路径的文件。
用户通常直接提供工单路径或编号。

## Wayfinder 操作

以下约定用于 `/wayfinder`，其探索状态与上述分诊状态分开使用。

- 地图：`.scratch/<effort>/map.md`，包含笔记、已作决策和待探索问题。
- 子工单：`.scratch/<effort>/issues/NN-<slug>.md`，从 `01` 编号，
  正文记录问题；`Type:` 为 `research`、`prototype`、`grilling` 或 `task`。
- 状态：未认领的开放工单使用 `Status: open`；
  认领后使用 `Status: claimed`，解决后使用 `Status: resolved`。
- 阻塞：顶部附近使用 `Blocked by: NN, NN`；
  列出的全部工单均为 `resolved` 时解除阻塞。
- 探索前沿：扫描开放、未阻塞且未认领的工单，优先选择编号最小者。
- 认领：开始工作前，将状态改为 `claimed` 并保存。
- 解决：在 `## Answer` 下追加结论，将状态改为 `resolved`，
  再将摘要和工单链接追加到地图的已作决策部分。
