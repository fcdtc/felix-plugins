---
name: video-summary
description: 下载 Bilibili 或 YouTube 字幕；无字幕时用 ASR 转写，并生成中文总结。用于视频逐字稿、指定时间范围转写与内容总结。
---

# 视频逐字稿与总结

先按输入平台阅读对应 reference：

- Bilibili：`references/bilibili.md`
- YouTube：`references/youtube.md`

所有动作统一通过：

```bash
python3 <SKILL_DIR>/scripts/launcher.py <action> ...
```

launcher 在 macOS Apple Silicon 上管理专用 CPython 3.12 环境。YouTube 还需要 Deno；ASR 需要 `ffmpeg`、`ffprobe`。

## 流程

1. 下载原语言字幕。
2. 无可用字幕时下载音频并执行 ASR。
3. 只接受当前命令输出且 `ok: true` 的 `RESULT_JSON`。`ERROR_JSON` 或 `ASR_ERROR_JSON` 是失败终态，不使用旧文件继续。
4. 启动全新独立总结 subagent，仅提供视频标题、`deliverables.verbatim`、`chunks`、`deliverables.summary` 绝对路径和总结要求。
5. subagent 必须覆盖逐字稿全文，生成中文结构化总结，并覆盖 summary 占位内容、保留一级标题。主 agent 不读取逐字稿或 chunk 正文，也不代写总结。
6. 运行：

```bash
python3 <SKILL_DIR>/scripts/launcher.py validate-delivery \
  --verbatim "<deliverables.verbatim>" \
  --summary "<deliverables.summary>"
```

只有 `DELIVERY_JSON.ok` 为 `true` 才交付 `逐字稿.md` 和 `总结稿.md`。平台无法创建独立 subagent 时停止，不在主上下文降级总结。

## ASR 模型预热（可选）

```bash
python3 <SKILL_DIR>/scripts/launcher.py setup [en|zh|all]
```

模型下载或长视频转写应在后台运行，并监控成功与失败终态。
