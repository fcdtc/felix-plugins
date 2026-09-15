# 01: 搭建 Web 应用骨架

**What to build:** 在 `examples/snake-web/` 中创建无第三方依赖的静态 Web 应用骨架，建立最终页面语义结构、稳定 DOM hooks、Canvas 棋盘容器、分数与最高分区域、游戏状态、方向控制、暂停和重开控件、操作说明、ES module 入口及 Node 内置测试入口；本票不实现蛇移动、碰撞或游戏循环。

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

## Scope

- 创建 `index.html`、`styles.css`、`package.json`、`src/main.js` 与 `tests/smoke.test.js`。
- `index.html` 使用 `<script type="module">` 加载 `src/main.js`，并一次性建立后续工单需要的稳定元素标识。
- 页面包含标题、当前分数、最高分、可感知的状态文本、带可访问名称的 Canvas、四个方向按钮、暂停/继续按钮、重新开始按钮和操作说明。
- 建立基础响应式壳，使页面与正方形棋盘能在常见桌面和窄屏宽度内布局，但最终交互细化留给 04。
- `package.json` 仅声明 `"type": "module"` 与调用 Node 内置测试的脚本，不添加 dependencies 或 devDependencies。
- smoke test 从文件和模块公开边界验证必要入口存在，不模拟尚未实现的游戏行为。

## Acceptance Criteria

- [ ] `examples/snake-web/` 可通过 `python3 -m http.server 8000 --directory examples/snake-web` 加载，页面具备约定的语义区域、稳定 DOM hooks、Canvas 与全部控件，ES module 无启动错误，`node --test examples/snake-web/tests/*.test.js` 通过最小 smoke test，且代码中没有伪造蛇移动或游戏循环。
