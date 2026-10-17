# YouTube

## 字幕优先

```bash
python3 <SKILL_DIR>/scripts/launcher.py youtube-subtitles <URL或ID> \
  [--start-seconds N] [--end-seconds N]
```

人工字幕按中文、英文优先；自动字幕只使用可确认的原语言轨，不使用机翻。失败终态 `no_usable_subtitle` 时进入 ASR。

## ASR 兜底

```bash
python3 <SKILL_DIR>/scripts/launcher.py youtube-asr <URL或ID> [en|zh] \
  [--start-seconds N] [--end-seconds N]
```

语言按视频原声选择；不确定时 YouTube 默认 `en`，中文视频用 `zh`。范围参数以秒计，例如前 7 分 20 秒为 `--end-seconds 440`。

## 下载策略

- 运行时：macOS Apple Silicon、专用 CPython 3.12、锁定版 `yt-dlp[default]`、本地 EJS、Deno。
- 鉴权：显式设置 `YT_COOKIES_FROM_BROWSER` 时只用该浏览器；否则依次裸请求、Chrome、Safari。
- 媒体：优先独立音频；仅在格式不可用时回退到含音频的最佳媒体，并提取 M4A。
- 缓存：按视频 ID 和时间范围复用；复用前由 `ffprobe` 校验。
- 隔离：忽略用户级 yt-dlp 配置，不启用远程 EJS；下载在临时目录完成，通过校验后原子发布。

内部缓存位于 `yt_temp/.work/<video-id>/<range>/`；交付目录仍按标题命名。

## 故障处理

- 环境准备失败：安装 CPython 3.12、Deno、ffmpeg/ffprobe 后重试。
- `metadata_failed`：检查网络、YouTube 登录和浏览器 Cookie 权限。
- `download_failed`：查看 `ERROR_JSON.error.attempts`；不要使用目录中的旧文件继续。
- `asr_quality_gate_failed`：报告质量问题，不交付低质量逐字稿。
