---
name: tech-visual
description: Build self-contained HTML pages that visualize technical concepts for short-video screen recording. Routes between a data-style (dark, multi-tab bar charts for accumulation/before-after metrics) and a concept-style (warm, two-actor split layout for protocols/roles/flows) paradigm, then enforces visual + copywriting rules tuned for "5-second skim" viewers. Use when the user asks to make a visualization / diagram / demo page for a technical principle (cache, tokens, protocol handshake, query plan, agent loop, etc.), needs a `visual.html` / `*_demo.html` for a video script, or says things like "把xx拆解画成图" / "做一个演示xx的网页" / "为这期视频配一个 visual".
disable-model-invocation: true
---

# 技术原理可视化演示页

把抽象的技术机制(缓存、握手、调用链、契约、流程)变成屏幕上一眼就懂的 HTML 演示页。每个演示是一个 self-contained HTML 文件(CSS+JS全部内联),用浏览器打开 + 切 tab + zoom 展示。

## 核心心法(默念三遍再下笔)

> **这是给"5 秒完播率下扫一眼的路人观众"看的,不是给"工程师视角的我自己"看的。**

工程师 default 偏好和路人偏好正好相反。当你纠结"这样够不够清楚"时,通常不是字号/对比/颜色不够好,而是你又站到工程师视角去了。每条具体规则都是这条心法的派生。

## Quick Reference(路由表)

| 用户意图 / 关键词 | 去看 |
|------------------|------|
| 数字、累积、命中率、token 分布、调用次数、前后对比 | `references/style-dark.md`(数据型骨架/CSS) |
| 是什么、谁负责什么、协议、角色对仗、流程图 | `references/style-warm.md`(概念型骨架/CSS) |
| 选哪种范式? 配色色板? 字体栈? | `references/colors-fonts.md` |
| 字号/留白/对齐/末端 label 偏移这类视觉硬规则 | `references/visual-rules.md` |
| label 怎么写? 中文 vs 英文? 术语 pill? 对仗结构? | `references/copywriting-rules.md` |
| 用户反对过什么做法? | `references/anti-patterns.md` |
| 小红书系列(`NNN_topic/`)文件命名/录屏集成 | `references/integration-xiaohongshu.md` |

**高频路由放前面**:90% 的请求第一步就是选范式 → 然后照搬骨架 → 然后查具体规则。

## 工作流(5 步)

1. **拿主题** — 用户给的脚本/需求里,哪一段需要"屏幕上要展示什么"
2. **选范式** — 看 `colors-fonts.md` 决策表;两种都不像默认 dark
3. **照骨架** — 加载对应 `style-dark.md` 或 `style-warm.md`,直接抄 CSS/HTML 骨架
4. **守规则** — 写每个 label/配色/段宽时对照 `visual-rules.md` + `copywriting-rules.md`
5. **自验证** — playwright 截图自查,按 `visual-rules.md` 末尾的"自验证清单"逐项过

## 选范式速查

| 主题特征 | 范式 | 视觉骨干 |
|---------|------|---------|
| 数字、累积、多状态演变(缓存命中、token 分布、查询次数、调用链增长) | **数据型 dark** | tab 切换 + 横向条形图 + 对比表格 + 公式盒 |
| 是什么、谁负责什么、协议、流程(Tool Use 契约、Agent 循环、TCP 握手、消息流向) | **概念型 warm** | 白卡片 + 顶部 hero quote + 二分对仗 + pill 术语 + 箭头协议 |

详见 `references/colors-fonts.md` 决策表。

## 五条核心铁律(其余规则在 reference 里)

这五条是路人视角的最低限度,违反任何一条就会"扫一眼看不懂"。

1. **字号底线 14px** — 录屏给路人看,不是给开发者读代码 → `visual-rules.md` 规则 9
2. **同概念跨视图必须同标签同色同位置** — 多 tab 切换靠视觉一致建立认知 → `visual-rules.md` 规则 2
3. **角色对立必须用饱和色对照,灰色只做装饰底** — 颜色就是语义载体 → `visual-rules.md` 规则 10
4. **术语只能从口播/原文里找,不要自创** — UI 看的字必须跟耳朵听的对得上 → `copywriting-rules.md` 规则 9
5. **改完自己用 playwright 截图自查,不让用户手动截** — 理论"差不多够"在实际渲染会重叠/裁切 → `visual-rules.md` 自验证清单

## 输出守则

1. 所有视觉/文案规则违反前先查对应 reference
2. 视觉里说出来的任何技术结论都要有出处(官方 docs / RFC / 实测数据),不确定时用中性表述
3. 每写完一段问自己"删了观众还能看懂吗?",能看懂就删 → `copywriting-rules.md` 信息密度规则 5
4. 不要塞与本期主题无关的内容(一期视频一个主题)

## 不触发场景

- 视频封面图 → 用 `xhscover-claude`
- 脚本口播词 → 用 `xiaohongshu-claude`
- 通用前端/Web 开发任务(跟"技术原理拆解"无关) → 用 `frontend-design`
- 装饰性插图、icon 设计
