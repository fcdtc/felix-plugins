# Draw.io 架构图集成指南

这份文档说明如何把 **draw.io 架构图**集成进 html-ppt 的 slide 里，重点是**深色主题画布下不出现白底/色块、文字不消失**。这是一个踩过坑、验证过的方案，不是猜测。

## 何时加载这份文档

当任务满足以下任一条件时，加载本文档：

- 用户要在 PPT 里放**架构图 / 流程图 / 拓扑图 / 时序图**等复杂图（不是简单几个方框，那种用 layout 直接画即可）
- 用户说"**集成 drawio / draw.io 图**"、"**把架构图导出成 svg 放进 PPT**"
- 你判断需要画一张**节点很多、层级很深、用 HTML+CSS 手画不划算**的图
- 深色主题（dracula / tokyo-night / catppuccin-mocha / terminal-green / blueprint 等）的 slide 里要嵌入外部 SVG 图，且出现了**白底 / 灰块 / 文字看不见**的问题

如果图很简单（≤6 个节点、纯线性流程），**不要用 draw.io**——直接用 layout 里的卡片 + 箭头画，更轻、配色天然统一。

## 工作流：subagent 画图 + 主流程集成（推荐）

复杂架构图最稳的做法是**让 drawio skill 在子 agent 里画，主流程只负责集成**，两边职责分离：

1. **启动 subagent 画图**：用 Agent 工具加载 drawio skill，让它生成 `.drawio` 源文件并导出成 SVG。给 subagent 的 prompt 要明确：图的主题、节点清单、层级关系、导出成 `.svg` 放到 deck 的 assets 目录。

   ```
   用 drawio skill 画一张「多智能体编排流程」架构图：输入 → Planner → 3 个 Agent 并行 → Aggregator → 输出。
   导出成 SVG，路径写到 examples/my-talk/assets/arch.svg。
   ```

2. **回到主流程做深色化 + 集成**：按下面的「深色化处理」和「嵌入方式」把 SVG 接进 slide。

这样分工的好处：drawio skill 专注把图画对，html-ppt 专注把图画进画布、配色融入主题。**不要让主 skill 同时操心 drawio XML 和 PPT 布局**，注意力会分散。

## 深色化处理（核心避坑）

draw.io 导出的 SVG 默认是**浅色 + 白底**，直接塞进深色画布会有一块刺眼的白色矩形。处理时务必记住下面几条，它们都是踩过坑回滚过的：

### ✅ 正确策略

1. **保留所有 `light-dark(浅,深)`，只强制 `color-scheme:dark`。**
   draw.io 新版给每个形状加了 `style="fill:light-dark(...);stroke:light-dark(...)"`。
   这是朋友不是敌人——浏览器在 `color-scheme:dark` 下会自动取第二个（深色）参数，用的就是 draw.io 官方调好的深色配色，对比度有保证。**不要手动把颜色一个个替换掉。**

2. **根 `<svg>` 改成透明深色**：把根 svg 上写死的 `style="background:#ffffff;...color-scheme:light dark"` 改成
   `background:transparent;background-color:transparent;color-scheme:dark`。

3. **清除残留底色块**：
   - 节点文字 div 上的 `background-color:#ffffff`（白底）→ 改 `transparent`
   - 边标签 div 上的 `background-color:light-dark(#ffffff,var(--ge-dark-color,#121212))`（深色模式变灰块）→ 改 `transparent`
   - 上述透明化之后，原来配在白底上的**深色文字**会看不见（`#1E3A8A` / `#065F46` / `#78350F` / `#991B1B` / `#831843` 这类）→ 同步换成亮色（如 `#a8d2ff` / `#a8ffcc` / `#ffd8a8` / `#ffb3ad`）

### ❌ 千万别做的坑

- **绝不要用 `style="[^"]*light-dark[^"]*"` → `style=""` 这种整段清空。**
  含 `light-dark` 的 style 里往往还混着 `font-size`、定位等布局属性，整段清空会把它们一起删掉，**文字直接全部消失**。只做精确替换（`background-color:#ffffff` → `transparent`），不要碰整个 style 串。
- **不要全局替换颜色 hex**去贴近 PPT 强调色。会误伤文字 div 和边标签。要改配色只能精准定位到具体节点的 fill/stroke 单独改。

## 嵌入方式：用 `<img>` 不用 `<object>`

```html
<!-- ✅ 正确：img 嵌入，深色画布上原生透明 -->
<img class="arch-svg" src="assets/arch.svg" alt="架构图">

<!-- ❌ 错误：object 会把 SVG 当独立文档加载，底色填白/填深色，透明不可靠 -->
<object type="image/svg+xml" data="assets/arch.svg"></object>
```

`<object>` 把 SVG 当独立文档加载，子文档默认背景常被填白/填深色，根 svg 的 `background:transparent` 在 object 上下文里不可靠。`<img>` 嵌入的 SVG 在深色画布上**原生透明**、矢量缩放不变。

如果这页要交互（拖动平移 / 滚轮缩放 / 双指捏合），给 `<img>` 加个 class，JS 里 `querySelector('.arch-svg')` 改它的 `style.transform` 即可，跟 `<object>` 时代写好的交互代码完全兼容，无需改 JS。

## 可复现的导出 + 深色化命令

```bash
cd <deck 目录>   # 例如 examples/my-talk

# 1. 重新导出（不加 -e，避免 ~2MB 的 content 冗余）
/Applications/draw.io.app/Contents/MacOS/draw.io -x -f svg -o assets/arch.svg src/arch.drawio

# 2. 深色化处理
python3 << 'PY'
import re
with open('assets/arch.svg') as f: s=f.read()
# 根 svg 强制深色透明
s=re.sub(r'(<svg\b[^>]*?)\s+style="[^"]*color-scheme:\s*light dark[^"]*"',
         r'\1 style="background:transparent;background-color:transparent;color-scheme:dark"',s)
# 节点文字白底 → 透明
s=s.replace('background-color: #ffffff','background-color: transparent')
# 边标签灰底 → 透明
s=s.replace('background-color: light-dark(#ffffff, var(--ge-dark-color, #121212))','background-color: transparent')
# 深色文字 → 亮色
for o,n in {'#1E3A8A':'#a8d2ff','#065F46':'#a8ffcc','#78350F':'#ffd8a8','#991B1B':'#ffb3ad','#831843':'#ffb3ad'}.items():
    s=s.replace(o,n)
open('assets/arch.svg','w').write(s)
PY
```

## 验证深色效果（Chrome headless 截图）

改完别只看源码——用 headless Chrome 渲染到 PNG，肉眼看或喂给 vision 模型确认透明度：

```bash
cat > /tmp/r.html << 'EOF'
<!DOCTYPE html><html><head><style>body{margin:0;background:<你的画布背景色>}</style></head>
<body><img src="file://<绝对路径>/assets/arch.svg" style="width:1300px"></body></html>
EOF
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless --disable-gpu --no-sandbox \
  --screenshot=/tmp/out.png --window-size=1340,1640 --hide-scrollbars \
  --default-background-color=00000000 "file:///tmp/r.html"
open /tmp/out.png
```

`--default-background-color=00000000` 让你看到**真实透明度**，而不是被默认白底骗了。

## 关键避坑（浓缩）

1. draw.io SVG 的 `light-dark()` 是朋友——保留它 + `color-scheme:dark` 即可，别手动替换颜色。
2. **永远不要整段清空含 `light-dark` 的 `style="..."`**，会删掉 `font-size` 让文字消失。只做精确的 `background-color` 替换。
3. 深色画布嵌入 SVG 用 **`<img>` 不用 `<object>`**。
4. 透明化文字 div 底色后，记得把配在白底上的**深色文字同步改亮**，否则字看不见。
5. 验证用 Chrome headless + `--default-background-color=00000000` 看真实透明度，别只看源码。
