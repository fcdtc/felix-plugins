# Skill 维护验收

修改本技能时，按下表验证文档、渲染器与统计，不调用真实评测或模型。真实评测审计需用户提供任务及版本，不能以合成页宣称线上效果通过。

## 命令

从仓库根运行，输出目录必须是新目录：

```bash
python3 -I -B -m unittest discover -s plugins/felix-skills/tests -p 'test_mia_eval_report.py'
python3 -I -B plugins/felix-skills/skills/mia-eval-audit/scripts/render_report.py demo --out /private/tmp/mia-eval-demo-new
python3 scripts/validate-skills.py
claude plugin validate ./plugins/felix-skills
git diff --check
```

## 不变式

| 场景 | 验收要求 |
| --- | --- |
| 四类合成case各一项 | 顺畅通过率为1/4，四类互斥，总数为4 |
| 少一个worker结果 | 该case保留证据不足及可打开的占位页，分母不变 |
| 版本不同或关键轨迹缺失 | 不进入顺畅通过 |
| 请求失败后修复成功 | 无遗留业务偏差时计恢复；不能因最终成功遮蔽重试 |
| 已确认业务缺陷 | 即使worker填pass，也不能计顺畅通过 |
| 仅有假设或思考缺失 | 不直接判业务失败；必要证据未知按覆盖规则处理 |
| 零case | 比率N/A，不显示100%或除零 |
| 重复/额外case、非法JSON、未知证据引用 | 明确报错，不静默忽略 |
| 注入HTML、脚本URL、目录穿越键 | 作为文本转义或拒绝，不能执行或越目录写入 |
| 缺少指标 | 显示未返回，不补0、不估算费用 |
| 部分取证清单 | 显著提示仅对已获取/已选择case统计，不称全任务通过率 |
| 多轮/重试 | 同case全轮证据审计，保留原失败，不按日志事件数量扩分母 |
| 模板更改 | 所有逐case及汇总由同一渲染器生成；旧HTML与当前JSON不一致时必须明确重建 |

浏览器可用时对合成页做一次视觉检查：桌面/手机可读，暗色可辨，筛选有文本标签，详情与返回链接可用，表格溢出仅在容器内。模板无网络依赖；浏览器缺失时说明未做视觉验收。

## 现场审计能力验收

本地单元测试不验证MIA权限、任意任务结构、最新CLI、真实多轮关联或子代理判断质量。首次在新环境使用时用用户提供任务的小样本确认字段结构，再覆盖全量；与文档已见形态不同则适配本次取证，不能改造真实记录来迎合样例。
