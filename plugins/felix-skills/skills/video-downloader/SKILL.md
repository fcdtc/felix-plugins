---
custom: true
name: video-downloader
description: Downloads videos from ANY website including those with dynamic loading. Extracts m3u8, mp4 URLs from pages using Playwright, then downloads with MAX QUALITY using yt-dlp.
disable-model-invocation: true
---

# Universal Video Downloader

Downloads videos from ANY website including those with dynamic content loading (SPA, JavaScript-rendered video players) with **MAXIMUM QUALITY** output.

## When to Use This Skill

- Downloading from websites NOT supported by yt-dlp directly
- Sites with dynamic video loading (m3u8, HLS streams)
- Video platforms that obfuscate URLs
- When you need the **highest possible quality** from any source
- When regular video-downloader skill fails

## Supported Video Types

- **m3u8 / HLS streams**: Most common for modern video sites
- **Direct MP4 URLs**: Static video links
- **DASH streams**: Adaptive bitrate streaming
- **Proxy URLs**: Videos accessed through proxy services

## How to Use

### Basic Download (Auto Max Quality)

```
Download video from https://example.com/video-page
```

### Specify Quality

```
Download 4K video from https://example.com/video-page
```

```
Download 1080p video from https://example.com/video-page
```

### Audio Only

```
Download audio from https://example.com/video-page
```

### List Available Qualities

```
Show all available qualities for https://example.com/video-page
```

## Example

**User**: "Download video from https://mview.iyf.tv/play/ABC123"

**Output**:
```
============================================================
Universal Video Downloader - MAXIMUM QUALITY
============================================================

[1/3] Extracting video URL from page...
  URL: https://mview.iyf.tv/play/ABC123
  Browser: Chromium (headless)
  Navigating to page...
  Found: https://cdn.example.com/video.m3u8?token=...
  Found 3 video URL(s)

[2/3] Analyzing available qualities...
[info] Available formats:
  137  mp4        1920x1080   1080p 4000k
  22  webm        1920x1080   1080p 4500k (best)
  136  mp4        1280x720    720p  2000k

[3/3] Downloading video...
  Quality: best
  Output: ~/Downloads/video_max_20250213.mp4
  Command: yt-dlp -o ~/Downloads/video_max_20250213 ...

 Download complete!
  File: video_max_20250213.mp4
  Size: 1.2 GB
  Duration: 121:20
  Bitrate: 4000 kbps

Saved to: ~/Downloads/
```

## Prerequisites

Install required tools:

```bash
# Install Python dependencies
pip3 install playwright requests yt-dlp

# Install Chromium browser
playwright install chromium

# Install FFmpeg (for merging video/audio streams)
# macOS:
brew install ffmpeg

# Ubuntu/Debian:
sudo apt install ffmpeg

# Verify installation
python3 download_video.py --check-deps
```

## Usage as Command Line Tool

```bash
# Download with best quality
python3 download_video.py "https://example.com/video"

# Download with specific quality
python3 download_video.py "https://example.com/video" -q 1080p

# Specify output filename
python3 download_video.py "https://example.com/video" -o "my_video.mp4"

# Increase wait time for slow-loading pages
python3 download_video.py "https://example.com/video" -w 20

# Only list available qualities
python3 download_video.py "https://example.com/video" --list-only

# Check dependencies
python3 download_video.py --check-deps
```

## Quality Options

| Quality | Resolution | Bitrate | File Size (1hr) |
|---------|------------|---------|-----------------|
| **best** (auto) | 最高可用 | - | - |
| 4K | 3840x2160 | 6000-8000 kbps | 2.7-4 GB |
| 1080p | 1920x1080 | 3000-5000 kbps | 1.4-2.3 GB |
| 720p | 1280x720 | 1500-2500 kbps | 700MB-1.1 GB |
| 480p | 854x480 | 800-1200 kbps | 360MB-450 MB |

## How It Works

1. **Playwright Extraction**: Launches headless Chromium browser
2. **Network Monitoring**: Intercepts all XHR/fetch requests
3. **URL Detection**: Finds m3u8, mp4, ts URLs from network traffic
4. **DOM Parsing**: Falls back to parsing page HTML for embedded videos
5. **Quality Analysis**: Uses yt-dlp to list available video formats
6. **Max Quality Download**: Selects highest resolution and merges streams

## Troubleshooting

### No video URL found
- Increase wait time: `-w 20` or `-w 30`
- Some sites require clicking play button (auto-handled)
- Check if video is in an iframe

### Not getting max quality
- Use `--list-only` to see all available formats
- Manually specify format code with `-f FORMAT_CODE`
- The site may only offer lower qualities

### Download fails
- URL may require authentication
- Token/expired URLs: download immediately after extraction
- Some sites have geographic restrictions

## Sources

- [Playwright](https://playwright.dev/python/)
- [yt-dlp](https://github.com/yt-dlp/yt-dlp)
- [HLS Specification](https://datatracker.ietf.org/doc/html/rfc8216)
