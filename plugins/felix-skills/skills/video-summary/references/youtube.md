# YouTube 处理细节

公共流程（两级降级、交付产物、总结流程、whisper 初始化）见 SKILL.md，本文只讲 YouTube 特有部分。

## 第一步：解析输入

从用户输入提取 11 位视频 ID：`watch?v=`、`youtu.be/`、`shorts/`、`embed/` 后的串，或裸 ID。

播放列表链接：只取其中单个视频，向用户确认处理哪一个（或全部逐个处理）。

## 第二步：下载字幕（主路径）

```bash
python3 <SKILL_DIR>/scripts/youtube_download_and_chunk.py <URL或ID>
```

- 字幕语言优先级：人工字幕（中文 > 英文）> 原语言自动字幕。**绝不用 YouTube 机翻字幕**（英文视频的机器生成中文字幕属于此类）——脚本会自动识别并跳过。
- 成功标准：stdout 出现 `RESULT_JSON:{...}`。
- 失败（无字幕 / 地区受限）：脚本打印 `ERROR: 没找到字幕`，进入第三步。

## 第三步：ASR 兜底（仅当上一步失败）

```bash
python3 <SKILL_DIR>/scripts/youtube_asr_fallback.py <URL或ID> [en|zh]
```

语言选择规则：
- **默认 `en`**：YouTube 内容以英文为主，whisper 英文识别最准。判断不准时直接用 en。
- 传 `zh`：仅当用户明确说视频是中文且无字幕。

成功标准同上：`RESULT_JSON` 出现。

## 产物位置

- `yt_temp/<VIDEO_ID>/逐字稿.md` — 完整文字稿（原文语言）
- `yt_temp/<VIDEO_ID>/总结稿.md` — 中文总结（由总结流程写入）

## 鉴权说明

YouTube 会做 bot 校验。脚本内置自动降级链：裸请求 → Chrome Cookie → Safari Cookie，并自动启用 yt-dlp 的 JS 挑战求解器（`--remote-components ejs:github`）。若全部失败，让用户设置 `YT_COOKIES_FROM_BROWSER=<浏览器名>` 后重试（Safari 受 macOS 沙箱限制可能无权限读取）。

## 脚本

- `scripts/youtube_download_and_chunk.py` — 字幕下载 + 分块（主路径）。
- `scripts/youtube_asr_fallback.py` — 音频下载（含 bot 校验降级链）+ mlx-whisper 转写（兜底）。
