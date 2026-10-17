import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT_DIR = Path(__file__).parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPT_DIR))
spec = importlib.util.spec_from_file_location('video_summary_youtube', SCRIPT_DIR / 'youtube.py')
youtube = importlib.util.module_from_spec(spec)
spec.loader.exec_module(youtube)


class YouTubeTests(unittest.TestCase):
    def test_base_args_are_isolated_and_local(self):
        args = youtube.base_args()
        self.assertEqual(args[:4], [sys.executable, '-I', '-m', 'yt_dlp'])
        self.assertIn('--ignore-config', args)
        self.assertIn('--no-update', args)
        self.assertIn('deno', args)
        self.assertNotIn('--remote-components', args)

    def test_auth_attempts_are_ordered(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual([item[0] for item in youtube.auth_attempts()],
                             ['anonymous', 'chrome', 'safari'])
        with mock.patch.dict(os.environ, {'YT_COOKIES_FROM_BROWSER': 'firefox'}, clear=True):
            self.assertEqual(youtube.auth_attempts(),
                             [('firefox', ('--cookies-from-browser', 'firefox'))])

    def test_time_range_validates_and_keys_cache(self):
        self.assertEqual(youtube.TimeRange(0, 440).key, '0-440')
        self.assertEqual(youtube.TimeRange(1.25, 2.5).key, '1.25-2.5')
        with self.assertRaises(youtube.YouTubeError):
            youtube.TimeRange(5, 5)

    def test_resolve_video_reuses_successful_auth(self):
        failed = subprocess.CompletedProcess([], 1, '', 'blocked')
        success = subprocess.CompletedProcess([], 0, json.dumps({'title': '标题'}), '')
        runner = mock.Mock(side_effect=[failed, success])
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(os.environ, {}, clear=True):
            context = youtube.resolve_video('J82Drae0ZRE', directory, runner=runner)
        self.assertEqual(context.auth_args, ('--cookies-from-browser', 'chrome'))
        self.assertEqual(context.video_id, 'J82Drae0ZRE')

    def test_automatic_subtitle_requires_known_original_language(self):
        self.assertIsNone(youtube.pick_subtitle({'automatic_captions': {'en': {}}}))
        self.assertEqual(youtube.pick_subtitle({
            'language': 'zh-CN', 'automatic_captions': {'zh': {}, 'en': {}}
        }), ('zh', False))

    def test_subtitle_uses_only_staged_file(self):
        context = youtube.VideoContext(
            'J82Drae0ZRE', 'https://www.youtube.com/watch?v=J82Drae0ZRE', '标题',
            {'subtitles': {'zh': {}}}, (), youtube.TimeRange(), Path('/unused'))

        def runner(args, **kwargs):
            output = Path(args[args.index('-o') + 1] + '.zh.vtt')
            output.write_text('WEBVTT\n\n00:00:00.000 --> 00:00:02.000\n正文\n', encoding='utf-8')
            return subprocess.CompletedProcess(args, 0, '', '')

        artifact = youtube.fetch_subtitle(context, runner=runner)
        self.assertEqual(artifact.text, '正文')

    def test_valid_cache_survives_title_change(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / '.work' / 'J82Drae0ZRE' / '0-440'
            workspace.mkdir(parents=True)
            audio = workspace / 'audio.m4a'
            audio.write_bytes(b'a' * 2048)
            (workspace / 'audio.json').write_text(json.dumps({
                'video_id': 'J82Drae0ZRE', 'range': '0-440', 'selector': 'cached'
            }), encoding='utf-8')
            context = youtube.VideoContext('J82Drae0ZRE', 'url', '新标题', {}, (),
                                           youtube.TimeRange(0, 440), workspace)
            with mock.patch.object(youtube, '_probe_audio', return_value=440):
                artifact = youtube.ensure_audio(context, runner=mock.Mock())
            self.assertTrue(artifact.cache_hit)
            self.assertEqual(artifact.path, audio)

    def test_format_fallback_extracts_m4a(self):
        with tempfile.TemporaryDirectory() as directory:
            context = youtube.VideoContext('J82Drae0ZRE', 'url', '标题', {}, (),
                                           youtube.TimeRange(0, 440), Path(directory) / 'work')
            calls = {'download': 0}

            def runner(args, **kwargs):
                if args[0] == 'ffprobe':
                    return subprocess.CompletedProcess(args, 0, json.dumps({
                        'streams': [{'codec_type': 'audio'}], 'format': {'duration': '440'}
                    }), '')
                calls['download'] += 1
                if calls['download'] == 1:
                    return subprocess.CompletedProcess(args, 1, '', 'Requested format is not available')
                template = args[args.index('-o') + 1]
                Path(template.replace('%(ext)s', 'm4a')).write_bytes(b'a' * 2048)
                return subprocess.CompletedProcess(args, 0, '', '')

            artifact = youtube.ensure_audio(context, runner=runner)
            self.assertEqual(calls['download'], 2)
            self.assertFalse(artifact.cache_hit)
            self.assertTrue(artifact.path.is_file())


if __name__ == '__main__':
    unittest.main()
