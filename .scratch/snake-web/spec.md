# Snake Web：Ticket Loop 端到端验证项目

Status: ready-for-agent

## Problem Statement

需要用一个边界明确、规模适中、可由浏览器直接运行的项目，真实验证 Ticket Loop 能否按依赖顺序执行多张 Task Ticket，并为每张新票建立独立 Task Session。项目应足够完整，能检验实现、测试、Git 提交和工单收尾，但不能依赖第三方服务或复杂工具链。

## Solution

在 `examples/snake-web/` 中实现一个原生 HTML、CSS、JavaScript 的贪吃蛇游戏。游戏使用 Canvas 绘制 20×20 棋盘，领域规则与浏览器接口分离，支持键盘和屏幕按钮、暂停/继续、重新开始、响应式布局、无障碍状态提示以及本地最高分。

实施拆为五张严格线性依赖的 Task Ticket。每张票负责一个清晰模块边界，后续票只通过既定接口集成，避免不同 Task Session 重复实现同一逻辑。

## User Experience

- 页面初始展示标题、当前分数、最高分、游戏状态、正方形棋盘、方向控制、暂停与重开控件和操作说明。
- 用户可用方向键或 WASD 开始并控制游戏，也可使用屏幕方向按钮。
- 蛇每次按固定节奏前进一格；一次 tick 最多接受一次有效转向，且不能直接反向。
- 吃到食物后蛇增长一格并增加 10 分，食物不得生成在蛇身上。
- 撞墙或撞到自身后游戏结束并停止推进。
- 用户可暂停、继续和重新开始；重新开始会重置蛇、当前分数、食物和运行状态。
- 最高分保存在 `localStorage`；存储不可用或数据损坏时，游戏仍可运行并回退为 0。

## Implementation Decisions

- 成品固定放在 `examples/snake-web/`，不得修改 Ticket Loop 或其他插件实现。
- 不使用框架、构建器、CDN、后端服务或第三方运行时依赖。
- 使用浏览器原生 ES modules；不要求通过 `file://` 打开。
- Canvas 使用 20×20 逻辑网格，在 CSS 中响应式缩放，并按 `devicePixelRatio` 保持清晰。
- 游戏速度固定；不加入等级、动态难度、音效、主题或网络功能。
- DOM 负责标题、分数、状态、控件、说明和辅助技术反馈；Canvas 只负责棋盘、蛇和食物的视觉绘制。
- `src/game-state.js` 是纯规则模块，拥有游戏状态、移动、碰撞、增长、计分和食物生成；棋盘尺寸与随机源可注入。
- `src/renderer.js` 只负责 Canvas 尺寸与绘制，不复制领域规则。
- `src/runtime.js` 负责固定 tick、暂停、结束和状态发布；调度器可注入，测试不得依赖真实等待。
- `src/input.js` 只把键盘与按钮映射为领域动作；方向是否合法由规则引擎唯一判断。
- `src/storage.js` 只负责最高分读写与安全降级。
- `src/main.js` 是薄组合根，只创建依赖、连接 DOM 与启动应用，不重新实现规则、渲染、输入或存储逻辑。
- 使用 Node.js 20+ 内置 `node:test` 与 `node:assert/strict`。`package.json` 只声明 ES module 模式和测试脚本，不声明 dependencies 或 devDependencies。

## Target Layout

```text
examples/snake-web/
├── index.html
├── styles.css
├── package.json
├── README.md
├── src/
│   ├── main.js
│   ├── game-state.js
│   ├── renderer.js
│   ├── runtime.js
│   ├── input.js
│   └── storage.js
└── tests/
    ├── smoke.test.js
    ├── game-state.test.js
    ├── runtime.test.js
    └── storage.test.js
```

## Accessibility and Responsive Requirements

- 窄屏页面不得产生水平滚动，Canvas 始终保持正方形并适配可用宽度。
- 所有按钮具有明确文本或可访问名称，并显示清晰的键盘焦点。
- 游戏状态与分数通过 DOM 文本暴露；开始、暂停、结束和分数变化通过 `aria-live` 区域通知。
- Canvas 具有可访问名称，关键状态不得只通过 Canvas 图像或颜色表达。
- 游戏相关按键应避免触发页面意外滚动，同时不得拦截无关按键。
- 避免非必要动画，并尊重 `prefers-reduced-motion`。

## Verification Decisions

- 标准测试命令：`node --test examples/snake-web/tests/*.test.js`。
- 本地运行命令：`python3 -m http.server 8000 --directory examples/snake-web`。
- 浏览地址：`http://localhost:8000/`。
- 规则测试使用注入的随机源，覆盖固定食物位置与无空位边界。
- Runtime 测试使用 fake scheduler，不使用 sleep 或真实时间。
- 最终手工验收覆盖：开始、移动、得分、暂停、继续、撞击失败、重新开始、页面刷新后读取最高分。
- 每张票完成时必须运行与该票相关的单测，并保证已有测试继续通过。

## Ticket Boundaries

- 01 建立最终 DOM hooks、静态骨架和测试入口，不伪造可玩的游戏。
- 02 独占纯规则模块及其测试，不修改浏览器集成。
- 03 独占 Canvas renderer、runtime 和对应测试，并对 `main.js` 做首次真实组合。
- 04 独占输入适配与最终响应式/无障碍交互，可调整既有 DOM 和样式但保持 hooks 稳定。
- 05 独占 storage、README 与完整回归，只修复集成问题，不扩展玩法。

## Out of Scope

- 多人模式、在线排行榜、账号、后端、遥测或云存储。
- 关卡、障碍物、加速机制、动态难度、音效或主题系统。
- 第三方库、包依赖、打包、转译、服务端渲染或旧浏览器兼容层。
- 自动部署、push、创建 PR 或发布网站。
- 修改 `plugins/felix-skills/skills/ticket-loop/` 下的实现。
