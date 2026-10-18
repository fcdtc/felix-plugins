import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPT_DIR))
spec = importlib.util.spec_from_file_location(
    'video_summary_bilibili_asr', SCRIPT_DIR / 'bilibili_asr_fallback.py')
bilibili_asr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bilibili_asr)


class BilibiliAsrTests(unittest.TestCase):
    def test_base_args_are_isolated_local_and_impersonated(self):
        args = bilibili_asr.yt_dlp_args()
        self.assertEqual(args[:4], [sys.executable, '-I', '-m', 'yt_dlp'])
        self.assertIn('--ignore-config', args)
        self.assertIn('--no-update', args)
        self.assertEqual(args[args.index('--impersonate') + 1], 'Chrome')
        self.assertNotIn('/opt/homebrew/bin/yt-dlp', args)

    def test_metadata_and_download_share_base_args(self):
        metadata = subprocess.CompletedProcess(
            [], 0, json.dumps({'title': '第二集'}), '')
        downloaded = subprocess.CompletedProcess([], 0, '', '')
        with tempfile.TemporaryDirectory() as directory:
            cookie = Path(directory) / 'cookies.txt'
            cookie.write_text('cookie', encoding='utf-8')
            with mock.patch.object(bilibili_asr, 'resolve_output_base', return_value=directory), \
                    mock.patch.object(bilibili_asr, 'write_netscape_cookie',
                                      return_value=str(cookie)), \
                    mock.patch.object(bilibili_asr.common, 'run',
                                      side_effect=[metadata, downloaded]) as runner:
                audio, title, _ = bilibili_asr.download_audio('BV123', 1)

            self.assertEqual(title, '第二集')
            self.assertEqual(Path(audio).name, 'audio.m4a')
            self.assertFalse(cookie.exists())
            self.assertEqual(runner.call_count, 2)
            for call in runner.call_args_list:
                args = call.args[0]
                self.assertEqual(args[:4], [sys.executable, '-I', '-m', 'yt_dlp'])
                self.assertIn('--ignore-config', args)
                self.assertEqual(args[args.index('--impersonate') + 1], 'Chrome')
                self.assertEqual(args[args.index('--cookies') + 1], str(cookie))
                self.assertEqual(args[args.index('--playlist-items') + 1], '2')
            download_args = runner.call_args_list[1].args[0]
            self.assertEqual(
                download_args[download_args.index('-f') + 1],
                '30280/30232/30216/bestaudio')

    def test_metadata_failure_falls_back_and_still_downloads(self):
        failed = subprocess.CompletedProcess([], 1, '', 'blocked')
        downloaded = subprocess.CompletedProcess([], 0, '', '')
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(bilibili_asr, 'resolve_output_base',
                                  return_value=directory), \
                mock.patch.object(bilibili_asr, 'write_netscape_cookie',
                                  return_value=False), \
                mock.patch.object(bilibili_asr.common, 'run',
                                  side_effect=[failed, downloaded]) as runner:
            _, title, _ = bilibili_asr.download_audio('BV123')
        self.assertEqual(title, 'BV123')
        self.assertEqual(runner.call_count, 2)

    def test_existing_audio_skips_download(self):
        metadata = subprocess.CompletedProcess(
            [], 0, json.dumps({'title': '第二集'}), '')
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / '第二集'
            output.mkdir()
            (output / 'audio.m4a').write_bytes(b'audio')
            with mock.patch.object(bilibili_asr, 'resolve_output_base',
                                  return_value=directory), \
                    mock.patch.object(bilibili_asr, 'write_netscape_cookie',
                                      return_value=False), \
                    mock.patch.object(bilibili_asr.common, 'run',
                                      return_value=metadata) as runner:
                bilibili_asr.download_audio('BV123')
        runner.assert_called_once()

    def test_cookie_is_cleaned_when_download_raises(self):
        metadata = subprocess.CompletedProcess([], 1, '', 'blocked')
        with tempfile.TemporaryDirectory() as directory:
            cookie = Path(directory) / 'cookies.txt'
            cookie.write_text('cookie', encoding='utf-8')
            with mock.patch.object(bilibili_asr, 'resolve_output_base',
                                  return_value=directory), \
                    mock.patch.object(bilibili_asr, 'write_netscape_cookie',
                                      return_value=str(cookie)), \
                    mock.patch.object(bilibili_asr.common, 'run',
                                      side_effect=[metadata, RuntimeError('download failed')]), \
                    self.assertRaisesRegex(RuntimeError, 'download failed'):
                bilibili_asr.download_audio('BV123')
            self.assertFalse(cookie.exists())


if __name__ == '__main__':
    unittest.main()
