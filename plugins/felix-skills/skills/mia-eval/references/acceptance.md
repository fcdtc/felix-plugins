# Skill 维护验收

维护本技能只运行离线测试、结构/统计回归和一次浏览器检查，不调用真实 MIA 评测或模型。合成页、重渲染和文档例子均不能证明在线审计质量或 Agent 效果提升。

## 命令

从仓库根执行，demo 输出使用新物理绝对目录。命令与 CLI 参数以当前脚本 `--help` 为准。

```bash
python3 -I -B -m unittest discover -s plugins/felix-skills/tests -p 'test_mia_eval_*.py'
python3 -I -B plugins/felix-skills/skills/mia-eval/scripts/render_report.py demo --out /private/tmp/mia-eval-demo-new
python3 -I -B scripts/validate-skills.py
claude plugin validate ./plugins/felix-skills
git diff --check
```

新增/更新 skill 后核对 Claude/Codex 注册、sidecar 和 compatibility；注册已覆盖时保持原配置。测试只用匿名合成数据，不提交真实用户证据、身份、签名 URL 或思考。

## 数据与统计

| 场景 | 验收要求 |
| --- | --- |
| 选定范围与占位 | 默认 ToEvaluate，平台失败/已评价/运行中不混入；选入后失败/缺报告仍保留分母及可打开占位；重复/额外 case、非法 JSON 拒绝 |
| 多轮与版本 | 同项全部必要轮次和实际 retry 保留，不增加案例分母；固定版关联缺失不补通过；v1/v2/v3 不混装或改号冒充重审 |
| 设计按 checks 加权 | 一例1条fail、另一例9条pass为9/10；全部检查不按ID/类型筛选，unknown计已知分母；零条N/A；缺报告/必要检查只报已知占比 |
| 工具按 calls 加权 | 一例1次fail、另一例9次pass为9/10；技能读取也属于调用；实际初错与retry各计一次，多个问题不重复计fail |
| 逐工具同一来源 | 只从tool_usage_review.calls分组，总数/pass/fail/unknown之和分别等于总调用统计；工具名、原因转义 |
| 各轴独立 | design案例占比名为“设计遵循通过案例占比”；execution与completion_gate保持案例分母；一轴失败不抹去另一轴已有通过，逐调用fail不自动整体fail |
| unknown与清单不全 | 已知unknown保留分母，零调用N/A；缺报告/不完整清单只声称已知调用正确占比，不以metrics补未知calls |
| 调用契约 | tool_usage_review及calls字段严格，复合ID唯一，pass/fail有真实引用；集合与全部packet索引无缺项，不仅长度相等；metrics完整等于长度、不完整不小于已知长度 |
| 新旧统计JSON | statistics_schema_version=2明确design_adherence=checks、tool_adherence=calls与定义；v1/v2工具null，不伪造calls；旧产物按其版本解释，不继续输出旧tool_usage键 |
| 四类及计数 | 合成四类各一例，后台smooth_rate=1/4，分类互斥总数4；零caseN/A、未返回usage是null而非0 |
| 纯重渲染 | 原manifest/判定JSON/证据不变，新目录重建所有详情和summary，重算状态计数一致；不覆盖原冻结模板和历史页 |

## 审计语义与 worker 输入

以下匿名正反例进入冻结 axis-examples 和对应回归，确保 worker 会读到权威规则；规则测试不等于真实模型判定质量验收。

| 场景 | 预期边界 |
| --- | --- |
| 正常介绍/短关怀 | 通知中的原任务介绍、原需求和有据短鼓励不仅因思考“选择Message”就算新增；按实际独立目的、额外内容与生成/push参数判断 |
| 独立新增帮助 | 额外帮助有实际参数证据才检查该版本前置；没有实际调用不造calls，独立遗漏仍可查 |
| 必要查询未收尾 | 非空next_cursor仍可续页、无完整替代且规则要求查完时，execution/completion_gate有独立失败证据；已知生产通知成功保留，不推断隐藏页具体漏发对象 |
| 补充排除约束 | 本轮排除领域reference细则时不扣该项；类型选择、指南加载、素材传递按本次允许范围检查；专项排除不写全局默认 |
| 平台失败/参数错误 | 请求及恢复处理正确可pass；错误参数被拒fail，首次fail与修正pass分别保留；前置错误不连带判错正确下游通知 |
| 准入错误已收尾 | 查重失败仍新增但所有对象/后续已收尾，design/tool_usage可fail而execution/completion_gate可pass；独立漏项另证fail |
| Mock/合法受阻 | 按可见契约认可成功，不查真实落盘；规则允许且独立项继续/如实交接可完成；首次不可用不自动挣扎 |
| 范围修订 | 固定用户原文、适用范围与前后判定；只修直接受影响项，其他独立错误保留；模板变更不改判，单轮豁免不全局化 |

## 取证、安全与生命周期

| 场景 | 验收要求 |
| --- | --- |
| 物理路径与顺序 | skill/scope路径解析物理路径；RUN_DIR为0700且Git忽略，freeze前仅acquisition；保持拒绝symlink/污染/已有runtime，不放宽保护 |
| 快照 | SKILL/reference/scripts/templates与scope哈希固定，复制时源变化拒绝；新增reference进入快照；worker/校验/summary用同一版本 |
| 精简但完整 | history完整时不重复debug；仅缺项message补查，thinking缺失不自动补；完整输入和全部调用必读、累计Prompt定位读取、自然语言不递归解析 |
| 自动引用定位 | input数组/mapped对象根类型与SHA正确，RFC6901根/数组/波浪号/斜线及嵌套JSON链可解析；错定位/哈希/路径拒绝，未注册引用unchecked非完整证明 |
| 首条身份反转 | reserve后按实际case_key+ordinal核对started；数组顺序反转不串例，错ordinal拒绝；启动拒绝回pending保留分母 |
| 报告早于退出 | report-ready不释放slot；finished只匹配实际宿主completed/退出，重复/错worker不双释放 |
| 初审返修共享容量 | reserve/running/repairing/repair-reserved占同一容量；返修预占→宿主接受→resume，拒绝回repair-pending；按available补位，不改宿主上限 |
| 持续推进 | completed通知后继续核验/补位/汇总，进度不作final暂停，不轮询全量代理日志；必要输入阻塞全部剩余工作才停 |
| HTML安全 | 用户文本和工具名转义，脚本URL/目录穿越拒绝；身份/签名URL/凭据/原思考不进HTML；下载独立空目录、解释器从安全目录运行且Python -I |

## 页面与交互

| 场景 | 验收要求 |
| --- | --- |
| 标题和MIA链接 | 标题与主题切换保留；评测MIA链接在元信息框外独立一行且不重复，缺/非法URL如实显示而不生成可执行链接 |
| 元信息 | 默认折叠，可鼠标及键盘展开/收起；范围、版本、完整性与限制在此或详情/JSON可查 |
| 综合表现 | 分类数量保留；设计遵循率为分项首行，案例指标相对顺序保持；工具遵循率内置分项表 |
| 工具下钻 | 默认关闭，点击“工具遵循率 · 展开”显示逐工具表，再点击收起；aria-expanded同步，键盘可用，表列完整 |
| 删除区块 | 独立顺畅通过率行、checks/calls长公式、分轴/Mock/合法受阻长说明、旧常驻工具调用正确性总数区、末尾全部case/搜索/筛选/数量/noscript不出现，对应JS不遗留 |
| 保留完整性表 | 综合表现内全case调用清单完整性表继续显示全部选定case；详情文件、返回链接及问题里的case链接有效，删全case末表不删分母 |
| 问题与详情 | 问题按P0/P1/P2，包含环节/原要求/实际影响/每个case经过及链接，不写建议；详情工具名统一“工具遵循率”，检查/逐调用/时间线/覆盖限制保留 |
| 离线浅暗主题 | 无网络依赖，主题切换可用，存储拒绝仍渲染；400px可读、16px侧边距，只有表格/代码容器横向滚动，不推动页面横向滚动 |

浏览器可用时一次桌面/手机、浅色/暗色视觉检查并实测两处折叠、主题及详情返回。浏览器缺失明确未执行。真实历史报告可在新私有输出目录做全量结构/计数回归，原数据保持不变；对外示例只用匿名数据。

## 未覆盖边界

离线测试不验证 MIA 权限、最新 CLI、任意历史结构、实际宿主并发数量或 worker 模型判断质量，也不测 token 优化收益。现场新环境先确认授权任务样本的实际字段，再覆盖全部选定范围；不能改造真实记录迎合示例。报告严格区分本地测试、离线渲染、引用校验与真实审计/模型运行。
