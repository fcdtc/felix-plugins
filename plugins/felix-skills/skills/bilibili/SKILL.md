---
name: bilibili
description: 下载 Bilibili 视频字幕，将其进行分块以供 LLM（大语言模型）处理，并生成高质量的总结。当用户提供 Bilibili BV 号或 URL，并希望获取视频内容的总结、核心要点或详细的分解时使用。
disable-model-invocation: true
---

# Bilibili 字幕下载器技能

此技能通过使用专用的 Python 脚本和子智能体 (sub-agent) 编排，自动化提取和总结 Bilibili 视频字幕的流程。

## ⚠️ 运行目录与产物位置（务必遵守）

* **在用户当前的项目根目录（cwd）下运行所有脚本**，**不要 `cd` 进 skill 目录**——否则产物会错误地落进 skill 目录。
* **产物输出位置**：`<运行脚本时的 cwd>/bili_temp/<BV_ID>/`。即默认落在**项目目录**。
* 脚本路径要用 skill base 目录的**绝对路径**（见本指令最上方 `Base directory for this skill: <SKILL_DIR>`），形如 `python3 <SKILL_DIR>/scripts/xxx.py`。
* 如需指定其它输出目录，设置环境变量 `BILI_OUTPUT_DIR=<目标目录>`，产物会落到 `<目标目录>/bili_temp/<BV_ID>/`。

## 总体流程（两级降级）

```
拉取官方字幕 ──成功──▶ 分块 + 总结
       │
       失败（视频无 CC 字幕 / 无 AI 字幕）
       ▼
下载音频 → mlx-whisper ASR 转写 → 分块 + 总结
```

**关键判断**：很多视频只有弹幕、没有任何字幕。`download_and_chunk.py` 报 `ERROR: 没找到字幕喵...` 或 yt-dlp `--list-subs` 仅返回 `danmaku` 时，即应进入 ASR 兜底，不要反复重试字幕接口。

## 1. 主流程：提取官方字幕

运行自带的脚本下载并分块字幕。普通视频，均为 BV 号开头
```bash
python3 <SKILL_DIR>/scripts/download_and_chunk.py <BV_ID>
```
* **登录检查**: 如果脚本输出 `QR_CODE_READY:<PATH>`，它将等待用户扫描二维码。您应该将此图像发送给用户。
* **保存 Cookie**: 成功登录后，脚本会自动将 Cookie 保存到 `~/.openclaw/workspace/bilibili_cookie.txt`。
* **依赖**：脚本需要 `requests bilibili-api-python`（缺时先 `pip3 install --break-system-packages`）。

## 2. 兜底流程：ASR 语音转写（无字幕时）

当字幕拉取失败时，运行 ASR 兜底脚本——它会下载音频、安装 mlx-whisper、用 large-v3 模型转写：
```bash
python3 <SKILL_DIR>/scripts/asr_fallback.py <BV_ID>
```
* **首次运行会较慢**：需下载 large-v3 模型（约 2.9G）。脚本用 `hf-mirror.com` 镜像 + curl 逐文件下载（带断点续传），约 15 分钟下完。**之后同语言转写会很快。**
* **转写速度**：Apple Silicon 上约比实时快 3~5 倍（20 分钟视频约 3~6 分钟转完）。
* **登录**：复用主流程保存的 Cookie（`~/.openclaw/workspace/bilibili_cookie.txt`），无需重新扫码。
* **长时间运行**：模型下载/转写可能超过单条命令的超时上限，建议 `nohup ... &` 后台运行，再用定时任务轮询 `RESULT_JSON` 是否产出。
* 输出约定与主流程一致（同样输出 `RESULT_JSON`，但多了 `"source": "asr"` 字段）。

## 交付产物（两条流程统一）

每个视频最终在 `bili_temp/<BV_ID>/` 下交付**两份 Markdown 文档**，缺一不可：

| 文件 | 说明 |
|------|------|
| **逐字稿.md** | 视频的完整文字稿（视频标题 + 正文）。由脚本自动生成，内容不再改动。 |
| **总结稿.md** | 结构化内容总结，**以独立文档形式交付**。脚本先创建占位文件，由下面的总结流程写入正文。 |

> `RESULT_JSON` 中的 `deliverables.verbatim` 和 `deliverables.summary` 给出这两份文件的绝对路径。`chunks` 仅供内部并行总结使用，不是交付物。

### 总结稿的生成与交付

1. 从 `RESULT_JSON` 取 `chunks` 列表，用子智能体（见下）逐块总结，再合并去重。
2. 把最终总结**写入** `deliverables.summary` 指向的 `总结稿.md`（覆盖占位内容），保留一级标题 `# <视频标题> —— 内容总结`。
3. 交付时向用户明确告知两份文件的路径，并以 `总结稿.md` 作为主要交付物（可在对话中同时展示其要点）。

## 处理输出（两流程通用）

解析脚本输出的 `RESULT_JSON`，其中：
* `deliverables.verbatim` / `deliverables.summary`：两份交付文档的路径（见上）。
* `chunks`：内部分块文件列表，命名格式：
  * 普通视频 (BV号): `bili_temp/<BV_ID>/<BV_ID>_chunk_0.txt`
  * 课程剧集 (EP号): `bili_temp/<EP_ID>/chunk_0.txt`

## Bilibili 课程 (Cheese) 工作流程

1.  **提取课程/剧集信息**: 使用课程专属脚本获取元数据和字幕。课程或者视频，往往由 SS 或者 EP 开头
    ```bash
    python3 <SKILL_DIR>/scripts/cheese_downloader.py <SS_ID or EP_ID>
    ```
    * **登录**: 脚本将生成一个 `bilibili_login_qr.png`。扫描它以登录。
    * **SS_ID 模式**: 如果提供 SS_ID（如 `ss123`），脚本将打印课程信息和所有剧集列表，需要使用具体的 EP_ID 来获取字幕。
    * **EP_ID 模式**: 如果提供 EP_ID（如 `ep456`），脚本将下载字幕并切分保存到 `bili_temp/ep456/` 目录，输出 `RESULT_JSON`。

## 子智能体指令

在生成用于总结的子智能体时，请使用以下提示词 (prompt) 模式。各分块总结产出后会合并、去重，最终写入 `总结稿.md`：

> 请阅读以下 Bilibili 视频字幕分块，并提供全面、准确的总结。
>
> **要求：**
> - 捕获所有关键的技术细节、具体的数据点和逻辑步骤。
> - 使用标题保持清晰的结构（Markdown 二级/三级标题）。
> - 明确主旨和可执行的要点。
> - 风格：专业、信息丰富且详细。
> - 直接输出 Markdown 正文，不要重复视频标题。
>
> **字幕文件：** [PATH_TO_CHUNK]

## 资源

- **脚本**: `scripts/download_and_chunk.py` - 处理 Bilibili API 交互和基于 Token 的安全分块。
- **脚本**: `scripts/asr_fallback.py` - 无字幕时的 ASR 兜底：yt-dlp 下载音频 + mlx-whisper(large-v3) 转写，输出约定与主流程一致。
- **脚本**: `scripts/cheese_downloader.py` - 课程（Cheese）字幕专用。