---
name: video-summary
description: 下载视频字幕，生成逐字稿和总结稿 
disable-model-invocation: true
---

# 视频字幕下载 + 中文总结（Bilibili / YouTube）

统一处理两类视频：根据用户输入自动识别平台，走「字幕下载 → ASR 兜底 → 中文总结」两级流程。本 skill 可由 Claude Code 与 Codex 执行，但 ASR 兜底依赖 macOS、Apple Silicon、Python 3、ffmpeg、yt-dlp、网络访问和足够磁盘空间。

执行前先从当前已加载的 `SKILL.md` 所在目录解析绝对路径 `<SKILL_DIR>`；该名称是下文的说明性占位符，不是假定存在的环境变量。下载媒体、安装依赖或预热模型前，先检查上述运行条件；不满足时停止并给出准备步骤，不直接修改系统环境。

## 第零步：平台识别（路由）

| 用户输入特征 | 平台 | 详细文档 |
|---|---|---|
| `bilibili.com`、BV 号、`ss`/`ep` 开头 | Bilibili | `references/bilibili.md` |
| `youtube.com`、`youtu.be`、`shorts/`、裸 11 位 ID | YouTube | `references/youtube.md` |

识别后**阅读对应 reference 文档**再执行，本文只描述两边共享的流程与约定。

## 初始化：whisper 准备（两边共享，仅需一次）

ASR 兜底依赖 mlx-whisper（en 用 turbo 模型，zh 用 large-v3，约 2.9G，经 hf-mirror 下载约 15 分钟）。若本机从未跑过，先执行共享初始化脚本预热：

```bash
python3 <SKILL_DIR>/scripts/setup_whisper.py [en|zh|all]   # 默认 all
```

之后所有平台脚本的 ASR 兜底会直接复用本地模型，转写很快。**长时间运行**：模型下载/转写可能超过单条命令超时，使用当前 agent 环境提供的后台任务与状态监控能力，并同时监控成功和失败终态；不要假设 `nohup` 是唯一实现。

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
python3 <SKILL_DIR>/scripts/validate_delivery.py \
  --verbatim "<deliverables.verbatim>" \
  --summary "<deliverables.summary>"
```

6. 只有命令退出码为 0 且输出 `DELIVERY_JSON` 中 `"ok": true`，才向用户交付两份文件路径；否则修复总结或报告阻塞，不宣布完成。

`chunks`、`asr_transcript.json` 和 `asr_quality.json` 是内部诊断产物，不是交付物。输出目录位于运行脚本时的 cwd，以净化并截断后的视频标题命名；脚本一律使用 `<SKILL_DIR>` 下的绝对路径执行。

## 资源

- **公共脚本**: `scripts/common.py` — 依赖检查、hf-mirror 模型下载、mlx-whisper 转写、分块 + `RESULT_JSON` 输出（平台脚本共享）。
- **公共脚本**: `scripts/setup_whisper.py` — 一键初始化依赖与模型预热。
- Bilibili / YouTube 平台脚本见各自 reference 文档。
