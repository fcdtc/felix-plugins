# 官方 CLI 取证

## 入口与边界

加载当前环境的官方 `miacli`，读取 evaluation、agent-design；需要补 Skill/Tool 固定版本再读取对应 subskill。CLI入口遵循 miacli 的本地仓库优先/远端固定包规则，不在本技能维护另一个版本真值。所有实际命令先核对该环境 `--help`。

先保留 URL 的 task_id、space_id、motif_id、agent_id 等已给字段。用 `evaluation task get`补齐关联 motif、评测类型、固定 Agent 和版本、数据集及输入映射。目标必须是Agent评测；Workflow或只有数据集而无运行的链接，明确区别并定位正确任务。空间不符时按miacli规则处理，不能切到错误空间继续。

环境 Header 从用户明确输入/目标项目规则取得。本技能不把某项目的 PPE 或测试身份写死为通用默认。仅在该项目已要求时携带 `PPE: <指定环境>`；其他身份从工程上下文核对，不复制历史 case 中的示例身份。CLI新版本提示按miacli处理，不自动升级。

本流程只调用官方CLI；导出响应给出的文件链接可下载文件本身，不用它反推私有API。认证被拒、工具能力缺失或无权限时记录缺口，不能让另一个代理/浏览器/手写HTTP绕过。

## 最小读取链

以下是命令形态，`pony-mia` 替换为当次官方入口，全局 Header/JSON选项放在子命令前；具体选项以help为准。

```text
evaluation task get --task-id <TASK>
evaluation task record-list --task-id <TASK> --page <N> --page-size <SIZE>
evaluation task export --task-id <TASK> --motif-id <MOTIF> --export-type <HELP确认的类型>
agent get --motif-id <MOTIF> --agent-id <AGENT> --version <VERSION>
agent session history --session-id <SESSION> --limit <N>
agent session debug --session-id <SESSION> --message-id <MESSAGE> [--offline]
```

1. 先取少量record样本观察结构，读取total、实际cycle、错误、会话及运行定位；再按分页读完全部轻量清单，不展开全量debug到主线程。
2. 使用record/data_item稳定身份建case，保存完整输入与映射；根据实际exec_cycle区分空占位与真实轮次。
3. 对没有可靠JSON语义的改后输出/人工标注等，按需导出；同时读取耗时/token表。导出不是所有输入字段的唯一来源。
4. 共享的指定Agent版本与固定资源只取一次，保存完整配置。Skill正文优先实际`skill_read/skill_read_file`回包；需要完整规则时补读固定组件版本及可达reference，避免拿当前草稿当历史。
5. 各worker按已知Session读取history，通过返回的message_id取得本case各轮debug，核对run/trace/目标版本；历史多页按当前help支持的游标取全必要前文。缺失某轮保留partial。

完成每一步都检查平台status_info/错误，而不只检查CLI退出码。取证失败与业务失败分开记录。

## URL 能否代替 CSV

**可以作为默认入口；是否覆盖某份 CSV 的全部字段必须逐列验证。**实证过的导出可能是ZIP内多个CSV，而非help名称暗示的单Excel；下载链接也可能嵌在status_msg文本中。按实际返回的可信文件地址处理，不假定固定download_url键。

| 信息 | 优先来源 | 回退与判定 |
| --- | --- | --- |
| 原始数据项输入 | record.data_item.data及映射配置 | 可能是JSON字符串内对象数组；不要只读空的data_item.input/output。导出可能省略source_url等输入字段 |
| 真正送入Agent的输入 | cycle.graph_input/input、debug.pe/user_input | 区分原始数据项与渲染后的User Prompt |
| 原始输出 | cycle.output、debug/history终态 | 保留原始，不用人工修改后的文本覆盖 |
| 修改后输出 | 导出明确列及已核实JSON字段 | exp_output/partial_exp_output的语义须用有值样本核验；不能凭字段名认领 |
| 人工评分/评论 | 导出明确列、已核实metric_scores内容 | 指标定义不等于实际评分；空值不等于通过 |
| 调用 | cycle.agent_tool_calls、debug.tool_calls_json、原始tool事件 | 按tool_call_id关联；有的简表缺error，补原始事件 |
| 可见思考 | debug.raw_events_json中的thinking_payload等实际字段 | runtime_segments可能只含text/tool。无思考事件记missing，不用最终输出或自己推测补齐 |
| 时间、token、终态 | debug.runtime_usage_json、原始observation/terminal事件、导出统计 | 避免重复累加多份累计快照。字段缺失null，正常结束不代表业务完成 |
| SP/PE及每轮模型输入 | debug.sp/pe、runtime_steps_json里的prompt_built、原始debug_payload | 快照优先，记录可见性；敏感信息留私有证据 |
| Skill/Tool固定配置 | Agent指定版本、component固定版本、实际读取回包 | skill_meta只含名称描述/版本时不是正文；缺文件树不可称完整设计 |
| 失败运行 | record.fail_type、cycle.exec_error、工具error和终态 | 失败case可能没有Session/debug，仍进分母 |

### 已见响应结构的提示（适配而非硬编码）

- `records[].data_item.data`、`upgrade_param`、debug的`*_json`字段常是JSON字符串；先检查类型再解析，仅解析明确的容器层。不能对普通用户文本递归尝试JSON到语义变形。
- `records[].exec_cycles[]`可能含`exec_cycle=0`的空占位、`exec_cycle=1`的真实输出；数组长度不能直接当对话轮数。只排除已核实为空的占位，真实失败轮次不可丢掉。
- record/session状态与cycle/tool状态枚举是不同域。官方文档“待人工评测/已人工评测”不能直接套在cycle.status。
- `debug.raw_events_json` 中可见思考曾位于 `payload.thinking_payload.text`，业务调用错误位于 `payload.tool_payload.error`；具体结构以本任务样本为准。
- 平台可能在`runtime_steps_json[].prompt_built`或原始debug事件中保留每轮message_list/prompt_parts/skills/tools。skills只有名字时仍需正式组件正文。

## case、多轮与快照一致性

case通常用 `task_id + record_id + data_id` 定位；JSON中ID转为十进制字符串保存。展示ordinal按完整记录的稳定顺序固定。CSV顺序可能不同，关联按已核实的session+cycle+run或数据项ID，不能按行号盲配。

同一数据项多个独立评测配置先按用户指定目标筛选，记录排除原因；同一目标下多轮全部进入同一case。真正的重新运行/尝试保留attempt身份和原结果，冻结截止时按平台明确选定结果确定评测对象，并展示其他尝试；平台不能明确时标关联不确定，不挑最好一次。多轮/重跑语义必须用实际任务样本核验，不能把单轮观察当永久协议。

manifest每个case保留message_ids及私有brief中的record/data/cycle/run映射；message_id从history/debug权威返回取得，不拿run_id替代，除非平台明确返回两者相同。

分页完成后核对去重数与total。total变化时按冻结截止核验差异；缺页保留scope_complete=false及scope_note。已知缺case可建证据不足占位；未知未取得的总集合不能假装是已完整列出的case。

## 私有落盘与解析安全

- 每次导出/ZIP独立新建空目录，脚本位于Skill目录，不从下载目录执行解释器。Python使用`-I`。
- 解压前检查成员路径，拒绝绝对路径、`..`、符号链接和超额尺寸；只取所需CSV/JSON/文本，不执行下载内容。
- 原始文件保留原字节及SHA-256；派生资料另存。JSON解析保持大整数无损，所有关联键对比原始字符串；CSV用标准csv解析器，不用物理行号切case。
- 认证token、签名下载URL和用户图片链接不写HTML或仓库。HTML使用短的去标识证据，原始思考单独保留；不把工具输出中的HTML作为模板执行。
- 跨case共享文件仅读；每个worker在自己的目录补证据。大日志先列结构/索引，再按当前检查范围读取，不把整个多case导出塞进一个代理上下文。

## 必须请求补充的边界

只有URL读不到关键材料、官方导出失败/过期、历史被清理、或用户有平台外修改/标注时，才请求对应CSV或原始debug。准确说明缺的列/轮次/版本，而不是再次索要整包已取得的材料。CSV通常也没有完整历史Skill包，缺基线时应请求对应版本资源而不是无效重复导出。
