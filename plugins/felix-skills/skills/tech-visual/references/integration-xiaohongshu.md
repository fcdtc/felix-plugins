# 小红书技术拆解视频集成约定

> 这是特定项目的工作流约定,如果你的场景不是"为小红书技术拆解视频做演示页",可以忽略本文件。

## 文件命名

- 数据型:`{topic}_demo.html`(例:`prefix_cache_demo.html`)
- 概念型:`visual_section{N}.html` 或 `visual_{topic}.html`(例:`visual_section2.html`)

放在视频脚本同目录下,典型结构:

```
NNN_topic/
├── script.md            # 口播脚本
├── visual.html          # 单一可视化(概念型多用)
├── prefix_cache_demo.html  # 多 tab 数据演示
└── ...
```

`NNN` 是视频系列编号(如 `052_claude_cache`、`053_tool_use`、`055_claude_cache2`)。

## 工作流

1. **拿主题** — 看视频脚本,找出哪一段需要"屏幕上要展示什么"的可视化
2. **选范式** — 数据型(dark) vs 概念型(warm),见 `colors-fonts.md` 决策表
3. **加载对应范式参考** — 读 `style-dark.md` 或 `style-warm.md` 里的骨架和配色
4. **写第一稿** — 直接遵守视觉/文案硬规则,不要让用户再提醒第二遍
5. **playwright 截图自验证** — 改完用 `playwright-cli` 截图,自己看一遍。**不要让用户每次手动截图**
6. **集成进脚本** — 在视频脚本的录屏步骤里指明"打开 NNN_topic/xxx.html"

## playwright 自验证(硬规则)

改完任何视觉调整必须自己截图验证:

```bash
playwright-cli open file:///full/path/to/visual.html
playwright-cli screenshot
```

不要让用户每次手动截图。截图后自己按 `visual-rules.md` 的"自验证清单"逐项检查。

## 录屏步骤集成

完成 HTML 后在视频脚本的录屏步骤里写:

```
📹 [N]. 浏览器打开 NNN_topic/xxx.html
📹 [N+1]. 切到"消息2:缓存命中"tab,zoom到token分布条
```

## 术语一致性

UI label(眼睛看的)必须跟口播脚本(耳朵听的)严丝合缝。

写 label 前先翻一遍口播脚本,把反复出现的核心动词作为词根。详见 `copywriting-rules.md` 第 9 条。

## 参考成品

- 数据型:`052_claude_cache/prefix_cache_demo.html`
- 概念型:`053_tool_use/visual_section2.html`
- 多视图累积:`055_claude_cache2/`
