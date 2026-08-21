---
name: video-summary
description: 下载视频字幕，生成逐字稿和总结稿 
disable-model-invocation: true
---

# 视频字幕下载 + 中文总结（Bilibili / YouTube）

统一处理两类视频：根据用户输入自动识别平台，走「字幕下载 → ASR 兜底 → 中文总结」两级流程。

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

之后所有平台脚本的 ASR 兜底会直接复用本地模型，转写很快。**长时间运行**：模型下载/转写可能超过单条命令超时，建议 `nohup ... &` 后台运行，再轮询 `RESULT_JSON` 是否产出。

## 总体流程（两级降级）

```
拉取官方字幕 ──成功──▶ 分块 + 总结
       │
       失败（视频无 CC 字幕 / 无 AI 字幕）
       ▼
下载音频 → mlx-whisper ASR 转写 → 分块 + 总结
```

具体命令见各平台 reference 文档。

## 交付产物（两边统一）

每个视频最终交付**两份 Markdown 文档**，缺一不可：

| 文件 | 说明 |
|------|------|
| **逐字稿.md** | 完整文字稿（视频标题 + 正文，原文语言保留）。由脚本自动生成，内容不再改动。 |
| **总结稿.md** | 结构化中文总结。脚本先创建占位文件，由总结流程写入正文。 |

- 路径由 `RESULT_JSON` 中的 `deliverables.verbatim` / `deliverables.summary` 给出；`chunks` 仅供内部并行总结使用，不是交付物。
- 输出目录落在**运行脚本时的 cwd** 下，目录名统一用**视频标题**命名（净化非法字符、截断 80 字符；拿不到标题时回退 ID）：`bili_temp/<视频标题>/` 或 `yt_temp/<视频标题>/`。不要 `cd` 进 skill 目录运行。
- 脚本路径用 skill base 目录的**绝对路径**：`python3 <SKILL_DIR>/scripts/xxx.py`。

## 总结稿生成流程（两边统一）

1. 从 `RESULT_JSON` 取 `chunks` 列表，用子智能体逐块总结，再合并去重。
2. 把最终总结**写入** `deliverables.summary` 指向的 `总结稿.md`（覆盖占位内容），保留一级标题 `# <视频标题> —— 内容总结`。
3. **总结稿必须是中文**——逐字稿可以是英文，总结稿不行。
4. 交付时向用户明确告知两份文件路径，以 `总结稿.md` 为主要交付物。

### 分块总结子智能体提示词模板

> 请为以下视频字幕片段生成一份**中文**详细总结。
> **要求：**
> - 捕获所有关键的技术细节、具体的数据点和逻辑步骤。
> - 使用标题保持清晰的结构（Markdown 二级/三级标题）。
> - 明确主旨和可执行的要点。
> - 风格：专业、信息丰富且详细。
> - 直接输出 Markdown 正文，不要重复视频标题。
>
> **字幕文件：** [PATH_TO_CHUNK]

## 资源

- **公共脚本**: `scripts/common.py` — 依赖检查、hf-mirror 模型下载、mlx-whisper 转写、分块 + `RESULT_JSON` 输出（平台脚本共享）。
- **公共脚本**: `scripts/setup_whisper.py` — 一键初始化依赖与模型预热。
- Bilibili / YouTube 平台脚本见各自 reference 文档。
