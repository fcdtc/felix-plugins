# 分诊标签

技能使用五个标准分诊角色。本仓库通过工单文件的 `Status:` 行
记录下表对应的字符串。

| 标准角色 | 本仓库状态字符串 | 含义 |
| --- | --- | --- |
| `needs-triage` | `needs-triage` | 等待维护者评估 |
| `needs-info` | `needs-info` | 等待报告者补充信息 |
| `ready-for-agent` | `ready-for-agent` | 需求明确，可交给代理自主实施 |
| `ready-for-human` | `ready-for-human` | 需要人工实施 |
| `wontfix` | `wontfix` | 不予处理 |

技能要求应用某个分诊角色时，使用对应的本仓库状态字符串。
后续调整命名时，修改本表的“本仓库状态字符串”列。
