---
name: video-summary
description: 下载视频字幕，生成逐字稿和总结稿 
disable-model-invocation: true
---

# 视频字幕下载 + 中文总结（Bilibili / YouTube）

统一处理 Bilibili 与 YouTube 视频，按「字幕下载 → ASR 兜底 → 中文总结」流程执行。本 skill 可由 Claude Code 与 Codex 使用。

先从当前 `SKILL.md` 所在目录解析绝对路径 `<SKILL_DIR>`。所有公开动作统一通过 `python3 <SKILL_DIR>/scripts/launcher.py <action> ...` 执行；launcher 首次运行时准备专用虚拟环境，后续自动复用，无需手工 activate。ASR 兜底会检查本机兼容性和 `ffmpeg` / `ffprobe` 等外部工具；条件不满足时停止并给出准备步骤。

## 第零步：平台识别（路由）

| 用户输入特征 | 平台 | 详细文档 |
|---|---|---|
| `bilibili.com`、BV 号、`ss`/`ep` 开头 | Bilibili | `references/bilibili.md` |
| `youtube.com`、`youtu.be`、`shorts/`、裸 11 位 ID | YouTube | `references/youtube.md` |

识别后**阅读对应 reference 文档**再执行，本文只描述两边共享的流程与约定。

## 初始化 ASR（可选）

只有字幕不可用、需要 ASR 兜底时才需预热模型：

```bash
python3 <SKILL_DIR>/scripts/launcher.py setup [en|zh|all]   # 默认 all
```

模型下载和长视频转写可能超过单条命令超时；使用当前环境的后台任务与状态监控能力，同时监控成功和失败终态。

## 总体流程（两级降级）

```
拉取官方字幕 ──成功──▶ 分块 + 总结
       │
       失败（视频无 CC 字幕 / 无 AI 字幕）
       ▼
下载音频 → mlx-whisper ASR 转写 → 分块 + 总结
```

具体命令见各平台 reference 文档。

## 交付流程（两边统一）

每个视频最终交付**两份 Markdown 文档**：`逐字稿.md` 和中文 `总结稿.md`。

1. 执行平台 reference 指定的字幕或 ASR 脚本。
2. 只接受**当前命令**输出的 `RESULT_JSON`；普通视频结果必须有 `"ok": true`。`ASR_ERROR_JSON` 是失败终态：报告其中的质量问题，不使用目录里的旧文件继续总结。
3. 从 `RESULT_JSON.chunks` 生成中文结构化总结，覆盖关键技术细节、数据和逻辑，再合并去重；多个 chunk 彼此独立时优先使用当前平台可用的并行任务能力，不支持委派时顺序处理。
4. 用最终正文覆盖 `deliverables.summary` 的占位内容，保留一级标题。
5. 运行最终交付校验：

```bash
python3 <SKILL_DIR>/scripts/launcher.py validate-delivery \
  --verbatim "<deliverables.verbatim>" \
  --summary "<deliverables.summary>"
```

6. 只有命令退出码为 0 且输出 `DELIVERY_JSON` 中 `"ok": true`，才向用户交付两份文件路径；否则修复总结或报告阻塞，不宣布完成。

`chunks`、`asr_transcript.json` 和 `asr_quality.json` 是内部诊断产物，不是交付物。输出目录位于运行脚本时的 cwd，以净化并截断后的视频标题命名；脚本一律使用 `<SKILL_DIR>` 下的绝对路径执行。

## 资源

- **公开入口**: `scripts/launcher.py` — 专用虚拟环境初始化、动作路由和后续复用。
- **公共脚本**: `scripts/common.py` — 运行时验证、ASR 转写、质量门禁和交付输出。
- **模型初始化**: `scripts/setup_whisper.py` — 在专用环境内检查并预热模型。
- Bilibili / YouTube 平台脚本见各自 reference 文档。
