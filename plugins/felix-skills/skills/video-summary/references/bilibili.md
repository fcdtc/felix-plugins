# Bilibili 处理细节

公共流程（两级降级、交付产物、总结流程、whisper 初始化）见 SKILL.md，本文只讲 Bilibili 特有部分。

## 运行目录

- 所有脚本在**用户当前项目根目录（cwd）**下运行，产物目录以**视频标题**命名：`<cwd>/bili_temp/<视频标题>/`（标题净化非法字符并截断 80 字符，拿不到标题时回退 BV_ID）。
- 如需指定其它输出目录：`BILI_OUTPUT_DIR=<目标目录>`，产物落到 `<目标目录>/bili_temp/<视频标题>/`。

## 1. 主流程：提取官方字幕

普通视频（BV 号开头）：
```bash
python3 <SKILL_DIR>/scripts/launcher.py bilibili-subtitles <BV_ID>
```

- **登录检查**：脚本输出 `QR_CODE_READY:<PATH>` 时会等待扫码，把二维码图片发给用户。
- **Cookie 保存**：登录成功后自动保存到 `~/.openclaw/workspace/bilibili_cookie.txt`，ASR 兜底复用，无需重新扫码。
- Python 依赖由 launcher 在 Skill 专用虚拟环境中自动安装并复用。

## 2. 兜底流程：ASR 转写（无字幕时）

很多 B 站视频只有弹幕、没有任何字幕。`download_and_chunk.py` 报 `ERROR: 没找到字幕喵...` 或 yt-dlp `--list-subs` 仅返回 `danmaku` 时，直接进 ASR，不要反复重试字幕接口：
```bash
python3 <SKILL_DIR>/scripts/launcher.py bilibili-asr <BV_ID> [P_NUM]
```
- Bilibili ASR 默认按中文识别。
- `P_NUM` 使用零基编号：`0` 表示 P1，`1` 表示 P2；字幕与 ASR 路径语义一致。
- 转写速度取决于本机硬件和模型；以脚本实际进度为准。
- 脚本会分段转写并执行重复、时间戳和覆盖门禁。当前命令输出 `RESULT_JSON` 且 `"ok": true` 才算成功；输出 `ASR_ERROR_JSON` 时停止并报告失败区间，不使用旧逐字稿继续总结。

## 课程（Cheese）工作流程

课程/番剧往往由 SS 或 EP 开头：
```bash
python3 <SKILL_DIR>/scripts/launcher.py bilibili-cheese <SS_ID or EP_ID>
```
- **登录**：脚本生成 `bilibili_login_qr.png`，扫它登录。
- **SS_ID 模式**（如 `ss123`）：打印课程信息和所有剧集列表，需再用具体 EP_ID 获取字幕。
- **EP_ID 模式**（如 `ep456`）：下载字幕并切分保存到 `bili_temp/<剧集标题>/`，输出 `RESULT_JSON`。

## 分块文件命名

- 普通视频：`bili_temp/<视频标题>/<BV_ID>_chunk_0.txt`
- 课程剧集 (EP号)：`bili_temp/<剧集标题>/chunk_0.txt`

## 脚本

- `scripts/bilibili_download_and_chunk.py` — Bilibili API 交互 + 基于 Token 的安全分块。
- `scripts/bilibili_asr_fallback.py` — ASR 兜底：下载音频并执行语音转写与质量校验。
- `scripts/bilibili_cheese_downloader.py` — 课程（Cheese）字幕专用。
