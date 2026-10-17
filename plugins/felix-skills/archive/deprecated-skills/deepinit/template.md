# 本地导航文档模板与规则

主流程在 [SKILL.md](SKILL.md)。本文中的 `NAV_FILE` 由 target 决定：Claude 使用 `CLAUDE.local.md`，Codex 使用 `AGENTS.override.md`。

## 字段设计原则

每个字段都必须帮助后续 agent 搜索或修改代码；不能降低定位成本的内容不写。

| 字段 | 用途 | 备注 |
|---|---|---|
| `<!-- Parent: -->` | 向上回溯 | 仅根目录省略 |
| `## Purpose` | 语义搜索 | 一句话并包含核心名词 |
| `## Key Files` | 文件导航 | 描述职责和改动场景 |
| `## Subdirectories` | 目录导航 | 指向子目录的 `NAV_FILE` |
| `## Working Here` | 修改约束 | 约定、测试入口、陷阱与跨目录依赖 |

不写时间戳，不复述 package manifest 中的外部依赖；Testing 和 Common Patterns 合并到 Working Here。

## 模板

```markdown
<!-- Parent: {相对路径}/{NAV_FILE} -->

<!-- Codex target only: Continue to follow this directory's AGENTS.md when it exists. -->

# {目录名}

## Purpose
{一句话：目录职责及其在项目中的位置。}

## Key Files
| File | What it does | When you'd touch it |
|---|---|---|
| `file.ts` | 核心职责 | 相关改动场景 |

## Subdirectories
| Directory | What's inside |
|---|---|
| `subdir/` | 一句话说明（see `subdir/{NAV_FILE}`） |

## Working Here
- 该目录必须遵守的约定
- 可从事实源核验的测试命令
- 常见陷阱与跨目录依赖
```

根目录省略 Parent 行。Claude target 省略 Codex 承接说明；Codex target 必须保留它，避免 `AGENTS.override.md` 遮蔽同目录团队规则。

## 空目录处理

| 条件 | 处理 |
|---|---|
| 有源码或配置文件 | 正常生成 |
| 无文件、无子目录 | 跳过 |
| 无文件、仅有子目录 | 生成仅含 Purpose + Subdirectories 的最小文件 |
| 仅有生成物 | 跳过 |
| 仅有配置文件 | 正常生成并说明配置用途 |

## 最小示例

```markdown
<!-- Parent: ../{NAV_FILE} -->

# components

## Purpose
可复用的界面组件。

## Subdirectories
| Directory | What's inside |
|---|---|
| `forms/` | 表单组件（see `forms/{NAV_FILE}`） |
```

生成时必须把所有 `{NAV_FILE}` 替换为真实文件名，不能把占位符写入最终导航文件。
