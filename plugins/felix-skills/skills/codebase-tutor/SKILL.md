---
name: codebase-tutor
description: "有状态的多课时代码库教学"
disable-model-invocation: true
---

# codebase-tutor

带状态的多课时教学 skill。每次调用生成一课中文 HTML 交互课程，产出到目标代码库根目录的 `.learn/`（隐藏目录，个人学习用，想共享手动 `git add -f .learn`）。每课自包含、浏览器直接打开、零构建。

目标用户：有编程能力、接手陌生代码库的工程师。代码、函数名、术语保留英文，其余中文。

## 入口：单命令状态机

收到 `/codebase-tutor` 或上述触发意图时，检查目标代码库根目录（用户指定了目录用它，否则当前目录）是否存在 `.learn/state.json`：

| 状态 | 动作 | 参考文档 |
|---|---|---|
| 无 workspace | init：建代码地图 + 大纲 + 第一课 | 读 `references/init.md` |
| 有 workspace | 生成下一课 | 读 `references/lesson.md` |
| 带参数（如 `regenerate 02`）或用户指定主题 | 特殊操作 | `references/lesson.md` 末节 |

两个分支各自只读对应的参考文档，不全部加载。

`.learn/` 结构（init 建立，后续维护）：

```
.learn/
  state.json          ← repo 路径、课程清单
  code-map.md         ← 代码地图：模块清单、入口点、依赖关系、典型链路
  outline.md          ← 课程大纲（骨架，深度动态调整）
  assets/             ← tutor.css + tutor.js，init 时从本 skill 的 assets/ 原样复制，永不重新生成
  index.html          ← 第一课 = 总览课 = 课程首页
  lessons/NN-slug.html
  learning-records/   ← 教学版 ADR
```

**每课的硬性组成**（缺一不算完成）：≥1 个执行步进器（真实代码逐行高亮 + 变量同步变化）、结尾 1 道三选一预测题（点击即反馈）。组件由 `assets/tutor.js` 的 `data-*` 属性驱动，agent 只填数据不写 JS；HTML 骨架照抄 `references/lesson-template.html`。

## 方法论（术语定义后使用）

- **Fluency（流畅错觉）**：看懂 ≠ 记住。读代码时的"懂了"是短期流畅感，不产生 Storage Strength —— 所以每课结尾必须有一道**预测题**，强迫检索练习，把流畅错觉当场戳破。
- **Storage Strength（长期记忆强度）**：由带阻力的检索（预测题答错再修正）积累，而非重读积累。课写得再顺滑也替代不了这一道题。
- **ZPD / 最近发展区**：下一课的深度与切入点由 learning-records 决定 —— 已掌握的不重讲，误判过的正面澄清，用户自己提出的**未解之谜**优先讲（在能力边缘、且有内在动机的地方，学习效率最高）。
- **代码地图**：`.learn/code-map.md`，模块级索引。它是选题与定位的起点，但每课涉及的代码必须现读原文 —— 地图只回答"在哪"，不回答"怎么回事"。
- **未解之谜**：用户提出但未展开的问题，记在 learning-records 里，是后续课程的第一优先级素材；解开时闭环标记。

## 原则

- 只讲代码现状，不涉及 git 历史
- 教学高度停在**设计思路与架构**层：讲作者的取舍与巧思，不逐行解析代码；亮点用简化伪代码呈现（细节见 `references/lesson.md` 的「内容高度」）
- 一课 10–15 分钟，一个完整可讲述单元（如一条请求的完整链路）
- 混合编排：大纲是骨架，每课深度和内容按 learning-records 动态调整
- 课程里出现的代码必须从真实文件原样复制（带文件路径），凭记忆重写的代码会悄悄失真
- 每课 HTML 顶部 frontmatter 记录依据文件清单 + commit hash；后续开课时先做 `git diff --quiet` 版本检测（细节见 `references/lesson.md`）
- 仅支持单仓库（repo 路径记在 `state.json`）；无 MISSION 文件，学什么由用户当场指定或按大纲推进；不做复习与间隔重复

## 学习记录

每课生成后写一条 learning-records（格式见 `references/learning-records.md`）：讲了什么、用户困惑与误判、新未解之谜、闭环标记。照实记录，不编造用户反应。
