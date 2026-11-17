# 设计检查与案例统计

## 设计遵循率

**设计遵循率 = 全部选定 case 的 checks 中 pass 条数 / 全部 checks 条数。** 每条即详情“预期与实际检查”表的一行。

- manifest 是案例集合真值。统计每例完整 checks，包括总检查和逐项检查；不按 ID、类型或工具筛选。
- pass、fail、unknown 各计一次，只有 pass 进入分子；跨 case 同 ID 分别计数。先加条数再算比例，不平均案例比例。例如一例 1 条 fail、另一例 9 条 pass，结果 9/10=90%。
- 零条或零 case 显示 N/A。unknown 留在已知分母；缺报告或必要检查时，只声称已知检查通过占比，保留缺项 case 数，不造未知条目。
- 状态取自 checks，不用 verdict 或逐调用判定替换。新增检查应对应真实适用要求，不为提高比例重复拆项。

## 案例轴与分类

| 指标 | 分子 / 分母 |
| --- | --- |
| 设计遵循通过案例占比 | design 检查 pass 的 case / 全部选定 case |
| 流程完成率 | execution 检查 pass 的 case / 全部选定 case |
| 结束时机正确率 | completion_gate 检查 pass 的 case / 全部选定 case |
| 顺畅通过率（仅后台统计） | 遵循设计、流程收尾且无恢复/挣扎的 case / 全部选定 case |

各案例轴独立，另一轴失败不抹去本轴已有证据。案例级缺检查为 unknown，分母仍保留。四类为顺畅通过、不通过、恢复/挣扎、证据不足，互斥且总数等于选定案例数。合法静默、保持、受阻与恢复边界见[审计标准](audit-rubric.md)。

`checks.tool_usage` 仍是必要案例检查，参与整体判定，也作为普通一条计入设计遵循率；其案例通过占比不是工具遵循率。工具遵循率按全部实际 calls 计算，唯一口径见[工具统计](tool-call-scoring.md)。

## 输出与历史兼容

新统计产物使用 **statistics_schema_version=2**，与 manifest/report 的 schema v1/v2/v3 分开：

- `design_adherence`：全部 checks 的总数、pass/fail/unknown、完整性与比率。
- `tool_adherence`：v3 全部 calls 的统计；v1/v2 没有调用判定时为 null，不从检查推算。
- `tool_adherence_by_tool`：从同一 calls 清单分组，不维护第二份手填计数。
- `case_axes`：design、execution、completion_gate 的独立案例统计。
- `total`、`counts`、`placeholder_cases`、`smooth_rate` 及 `metric_definitions`：集合、分类、缺报告和指标定义。

函数语义明确分开：`design_adherence_stats` 统计 checks，`tool_adherence_stats` 统计 calls。历史 `tool_usage_stats` 仅作为 calls 函数的兼容别名；旧 `tool_adherence_stats=checks` 的语义不继续保留。读取旧统计 JSON 必须按其版本和定义解释，不能只凭同名字段认领新指标。新产物不输出旧 `tool_usage` 键；原文件保持原样，以选定报告重新计算新统计。

汇总首行展示设计遵循率，其余案例指标保持相对顺序；工具指标内嵌可折叠逐工具表。综合表现保留分类数量和全 case 调用清单完整性表；公式、长说明、独立顺畅通过率行和末尾 case 搜索表省略。审计范围与完整性仍在折叠元信息、详情和私有 JSON 中。

删展示不取消判定：缺报告、必要检查、调用清单和取证范围仍需校验。汇总不展示覆盖率六行表，详情“覆盖与限制”保留。历史重渲染与口径变更分支见[报告修订](revisions.md)。
