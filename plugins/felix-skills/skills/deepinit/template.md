# CLAUDE.local.md 模板与规则

主流程在 [SKILL.md](SKILL.md)。本文件是生成 CLAUDE.local.md 时查阅的参考：字段模板、空目录处理、最小示例。

## 字段设计原则

每个字段都必须回答一个问题——**"这节能帮 Claude Code 后续 search/写代码时少走弯路吗？"** 不能回答的字段就是噪音，不写。

| 字段 | 服务于 | 备注 |
|------|--------|------|
| `<!-- Parent: -->` | 向上层级回溯 | 仅根目录省略 |
| `## Purpose` | 语义 search 最高频命中 | 一句话，必须含该目录的核心名词 |
| `## Key Files` | 文件级导航 + 何时改动 | 描述要含动词，说清职责而非文件类型 |
| `## Subdirectories` | 目录级导航 | 指向子目录的 CLAUDE.local.md |
| `## Working Here` | 写代码时的约束与坑 | 约定、测试入口、常见陷阱、跨目录依赖 |

**不写的字段**（会过期或与事实源重复）：
- 时间戳（agent 不靠时间戳导航，且会过期）
- 外部依赖（package.json / go.mod 已是事实源，重述即过期缓存）
- 拆分的 Testing / Common Patterns（合并进 `## Working Here`，避免碎片化）

## 模板

```markdown
<!-- Parent: {相对路径}/CLAUDE.local.md -->

# {目录名}

## Purpose
{一句话：这个目录是什么，以及它在整个项目里扮演什么角色。}
{必须包含后续 agent 最可能用来 search 的核心名词。}

## Key Files
| File | What it does | When you'd touch it |
|------|--------------|---------------------|
| `file.ts` | 该文件的核心职责（含动词，而非只说文件类型） | 什么改动会需要动到它 |

## Subdirectories
| Directory | What's inside |
|-----------|---------------|
| `subdir/` | 一句话说明（see `subdir/CLAUDE.local.md`）|

## Working Here
{写代码时必须知道的事，按需写以下几点，没用的点不要凑：}
- 约定：这个目录里代码必须遵守的规则（命名、导出方式、风格）
- 测试入口：改动后如何验证（具体命令或测试文件位置）
- 常见坑：容易踩的陷阱、历史遗留问题
- 跨目录依赖：依赖或被依赖的其他模块（指向它们的 CLAUDE.local.md）
```

根目录 CLAUDE.local.md：省略 `<!-- Parent: -->` 一行，其余相同。

## 空目录处理

生成前先判断目录是否值得生成。空目录或纯生成物目录不产 CLAUDE.local.md，避免噪音：

| 条件 | 处理 |
|------|------|
| 有源码或配置文件 | 正常生成 |
| 无文件、无子目录 | **跳过**——不生成 |
| 无文件、仅有子目录 | 生成最小 CLAUDE.local.md（只含 Purpose + Subdirectories） |
| 仅有生成物（*.min.js、*.map、dist） | 跳过 |
| 仅有配置文件（package.json、tsconfig） | 正常生成，Purpose 说明配置用途 |

最小模板（仅有子目录的容器目录）：

```markdown
<!-- Parent: ../CLAUDE.local.md -->

# {目录名}

## Purpose
{容器目录：组织什么用的。}

## Subdirectories
| Directory | What's inside |
|-----------|---------------|
| `subdir/` | 一句话说明（see `subdir/CLAUDE.local.md`）|
```

## 最小示例

根目录：

```markdown
# my-project

## Purpose
Web 应用，管理用户任务并支持实时协作。

## Key Files
| File | What it does | When you'd touch it |
|------|--------------|---------------------|
| `package.json` | 依赖与脚本 | 加依赖、改启动命令 |
| `tsconfig.json` | TypeScript 配置 | 调整编译选项 |

## Subdirectories
| Directory | What's inside |
|-----------|---------------|
| `src/` | 应用源码（see `src/CLAUDE.local.md`）|
| `tests/` | 测试套件（see `tests/CLAUDE.local.md`）|

## Working Here
- 改 package.json 后必须重装依赖
- 使用 TypeScript strict 模式
- 测试命令：`npm test`
```

嵌套目录：

```markdown
<!-- Parent: ../CLAUDE.local.md -->

# components

## Purpose
可复用的 React 组件，按功能与复杂度组织。

## Key Files
| File | What it does | When you'd touch it |
|------|--------------|---------------------|
| `index.ts` | 所有组件的 barrel 导出 | 加新组件后更新导出 |
| `Button.tsx` | 主按钮组件 | 改按钮样式或行为 |

## Subdirectories
| Directory | What's inside |
|-----------|---------------|
| `forms/` | 表单组件（see `forms/CLAUDE.local.md`）|

## Working Here
- 每个组件独立文件，通过 index.ts 导出
- 使用 CSS modules
- 单测在 `__tests__/`，命令 `npm test components`
```
