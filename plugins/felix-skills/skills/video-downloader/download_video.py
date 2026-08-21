#!/usr/bin/env python3
"""
Universal Video Downloader - Implementation Script
Downloads videos from ANY website using Playwright for URL extraction
and yt-dlp for downloading with MAXIMUM QUALITY.
"""
import argparse
import asyncio
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse, parse_qs


def check_dependencies():
    """Check if required tools are installed."""
    # Check Python packages
    for module in ['playwright', 'requests', 'yt_dlp']:
        try:
            __import__(module.replace('-', '_'))
            print(f"  {module}: OK")
        except ImportError:
            print(f"  {module}: MISSING")
            return False

    # Check yt-dlop CLI
    result = subprocess.run(['yt-dlp', '--version'], capture_output=True, text=True)
    if result.returncode != 0:
        print("  yt-dlp CLI: MISSING")
        return False
    print(f"  yt-dlp CLI: OK (v{result.stdout.strip()})")

    # Check FFmpeg
    result = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True)
    if result.returncode != 0:
        print("  FFmpeg: MISSING (quality merging may not work)")
    else:
        print("  FFmpeg: OK")

    return True


def extract_video_url_playwright(url, wait_time=10):
    """
    Extract video URL from a webpage using Playwright.
    Handles dynamic loading and SPA-style sites.
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("Error: Playwright not installed. Run: pip3 install playwright")
        return None

    print(f"\n[1/3] Extracting video URL from page...")
    print(f"  URL: {url}")
    print(f"  Browser: Chromium (headless)")

    video_urls = set()

    async def run():
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
                viewport={'width': 1920, 'height': 1080}
            )
            page = await context.new_page()

            # Monitor network requests
            async def handle_route(route):
                request = route.request
                url_str = request.url

                # Intercept video-related requests
                if any(pattern in url_str for pattern in ['.m3u8', '.mp4', '.ts', '.f4v']):
                    print(f"  Found: {url_str[:80]}...")
                    video_urls.add(url_str)

                await route.continue_()

            await page.route('**/*', handle_route)

            try:
                print(f"  Navigating to page...")
                await page.goto(url, wait_until='domcontentloaded', timeout=60000)

                print(f"  Waiting {wait_time}s for video player to load...")
                await asyncio.sleep(wait_time)

                # Try to find and click play button
                play_selectors = [
                    'button.play',
                    'button[title*="Play"]',
                    '.video-play-btn',
                    '#play-button',
                    '[class*="play"]',
                    '[aria-label*="play"]',
                ]

                for selector in play_selectors:
                    try:
                        element = await page.query_selector(selector)
                        if element:
                            await element.click()
                            print(f"  Clicked play button: {selector}")
                            await asyncio.sleep(5)
                            break
                    except:
                        pass

            except Exception as e:
                print(f"  Page load error (continuing): {e}")

            # Parse page content for embedded videos
            print(f"  Parsing page DOM...")
            page_content = await page.content()

            patterns = [
                r'["\']https?://[^"\']+\.m3u8[^"\']*["\']',
                r'["\']https?://[^"\']+\.mp4[^"\']*["\']',
                r'"url"\s*:\s*"([^"]+)"',
                r'"src"\s*:\s*"([^"]+)"',
                r'"playUrl"\s*:\s*"([^"]+)"',
                r'data-url\s*=\s*["\']([^"\']+)["\']',
                r'videoUrl["\']?\s*[:=]\s*["\']([^"\']+)["\']',
            ]

            for pattern in patterns:
                matches = re.findall(pattern, page_content)
                for match in matches:
                    if '.m3u8' in match or '.mp4' in match:
                        video_urls.add(match)

            await browser.close()

    try:
        asyncio.run(run())
    except Exception as e:
        print(f"  Playwright error: {e}")

    if not video_urls:
        print("  No video URLs found!")
        return None

    print(f"  Found {len(video_urls)} video URL(s)")

    # Choose the best URL - prefer m3u8 streams
    best_url = None
    for candidate in sorted(video_urls, key=len, reverse=True):
        if '.m3u8' in candidate:
            best_url = candidate
            break
        elif '.mp4' in candidate:
            best_url = candidate
            break

    if not best_url and video_urls:
        best_url = list(video_urls)[0]

    return best_url


def get_available_qualities(video_url):
    """Get list of available video qualities."""
    print(f"\n[2/3] Analyzing available qualities...")

    try:
        result = subprocess.run(
            ['yt-dlp', '-F', video_url],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode == 0:
            print(result.stdout)

            # Parse formats to find best quality
            formats = []
            for line in result.stdout.split('\n'):
                if re.match(r'^\d+ \w+', line):
                    parts = line.split()
                    if len(parts) >= 2:
                        format_code = parts[0]
                        extension = parts[1]
                        # Extract resolution if available
                        resolution = 'unknown'
                        for part in parts:
                            if re.match(r'^\d+x\d+$', part):
                                resolution = part
                            elif re.match(r'^\d+p$', part):
                                resolution = part
                        formats.append({
                            'code': format_code,
                            'ext': extension,
                            'resolution': resolution
                        })

            return formats
        else:
            print(f"  Error analyzing formats: {result.stderr}")
            return []

    except Exception as e:
        print(f"  Error: {e}")
        return []


def download_video(video_url, output_path, quality='best'):
    """Download video with specified quality."""
    print(f"\n[3/3] Downloading video...")
    print(f"  Quality: {quality}")
    print(f"  Output: {output_path}")

    # Build yt-dlp command for max quality
    cmd = [
        'yt-dlp',
        '-o', output_path,
        '--merge-output-format', 'mp4',
        '--keep-video',
        '--newline',
    ]

    # Add quality selection
    if quality == 'best':
        cmd.append('-f')
        cmd.append('bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best')
    elif quality == '4k':
        cmd.append('-f')
        cmd.append('bestvideo[height<=2160]+bestaudio/best[height<=2160]')
    elif quality == '1080p':
        cmd.append('-f')
        cmd.append('bestvideo[height<=1080]+bestaudio/best[height<=1080]')
    elif quality == '720p':
        cmd.append('-f')
        cmd.append('bestvideo[height<=720]+bestaudio/best[height<=720]')
    else:
        cmd.append('-f')
        cmd.append(quality)

    cmd.append(video_url)

    print(f"  Command: {' '.join(cmd[:6])}...")

    result = subprocess.run(cmd, check=False)

    if result.returncode == 0:
        # Get downloaded file info
        filename = str(Path(output_path).with_suffix('')) + '.mp4'
        if os.path.exists(filename):
            size = os.path.getsize(filename) / (1024 * 1024)
            print(f"\n Download complete!")
            print(f"  File: {os.path.basename(filename)}")
            print(f"  Size: {size:.1f} MB")

            # Try to get video info
            try:
                info_result = subprocess.run(
                    ['ffprobe', '-v', 'error', '-show_entries',
                     'format=duration,bit_rate,format_name',
                     '-of', 'default=noprint_wrappers=1', filename],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if info_result.returncode == 0:
                    for line in info_result.stdout.split('\n'):
                        if '=' in line:
                            key, value = line.split('=', 1)
                            if key == 'duration':
                                mins = int(float(value)) // 60
                                secs = int(float(value)) % 60
                                print(f"  Duration: {mins}:{secs:02d}")
                            elif key == 'bit_rate':
                                print(f"  Bitrate: {int(value)/1000:.0f} kbps")
            except:
                pass

        return True
    else:
        print(f"  Download failed with code {result.returncode}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description='Download videos from ANY website with MAXIMUM QUALITY'
    )
    parser.add_argument('url', help='Video page URL')
    parser.add_argument('-q', '--quality', default='best',
                        choices=['best', '4k', '1080p', '720p', '480p'],
                        help='Video quality (default: best/highest available)')
    parser.add_argument('-o', '--output', default='',
                        help='Output filename (default: auto-generated)')
    parser.add_argument('-w', '--wait', type=int, default=10,
                        help='Wait time for page load in seconds (default: 10)')
    parser.add_argument('--list-only', action='store_true',
                        help='Only list available qualities, do not download')
    parser.add_argument('--check-deps', action='store_true',
                        help='Check if required dependencies are installed')

    args = parser.parse_args()

    if args.check_deps:
        print("Checking dependencies...")
        if check_dependencies():
            print("\n All dependencies installed!")
            return 0
        else:
            print("\n Some dependencies missing. Install with:")
            print("  pip3 install playwright requests")
            print("  brew install ffmpeg  # macOS")
            print("  playwright install chromium")
            return 1

    if not args.url:
        parser.print_help()
        return 1

    print("=" * 60)
    print("Universal Video Downloader - MAXIMUM QUALITY")
    print("=" * 60)

    # Extract video URL
    video_url = extract_video_url_playwright(args.url, args.wait)
    if not video_url:
        print("\n Failed to extract video URL!")
        return 1

    print(f"\n Selected video URL: {video_url[:80]}...")

    # Get available qualities
    formats = get_available_qualities(video_url)

    if args.list_only:
        print("\n Available qualities listed above.")
        print(f"\n Video URL: {video_url}")
        return 0

    # Determine output path
    if args.output:
        output_path = args.output
    else:
        timestamp = time.strftime('%Y%m%d_%H%M%S')
        output_path = f"~/Downloads/video_max_{timestamp}"

    # Expand tilde
    output_path = os.path.expanduser(output_path)

    # Download video
    success = download_video(video_url, output_path, args.quality)

    return 0 if success else 1


if __name__ == '__main__':
    sys.exit(main())
