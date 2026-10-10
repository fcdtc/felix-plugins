import importlib.util
import io
import json
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock


SCRIPT_DIR = Path(__file__).parents[1] / 'scripts'
spec = importlib.util.spec_from_file_location('video_summary_common', SCRIPT_DIR / 'common.py')
common = importlib.util.module_from_spec(spec)
spec.loader.exec_module(common)


def word(text, start, end):
    return {'word': text, 'start': start, 'end': end, 'probability': 0.99}


def segment(text, start, end):
    return {'text': text, 'start': start, 'end': end,
            'words': [word(text, start, end)], 'compression_ratio': 1.2,
            'avg_logprob': -0.1, 'no_speech_prob': 0.01, 'temperature': 0.0}


def transcript(segments, duration=120):
    return {'text': ''.join(item['text'] for item in segments), 'segments': segments,
            'language': 'zh', 'audio_duration': duration}


class TranscriptQualityTests(unittest.TestCase):
    def test_normal_transcript_passes(self):
        data = transcript([
            segment('这是第一段正常内容', 0, 20),
            segment('这里继续讨论不同的技术主题', 20, 45),
            segment('最后给出总结和实践建议', 45, 80),
            segment('结束语', 80, 119),
        ])
        self.assertTrue(common.assess_transcript(data, 120)['ok'])

    def test_accident_scale_alternating_loop_is_rejected(self):
        segments = [segment('前面的正常内容', 0, 10)]
        for index in range(1060):
            start = 10 + index * 0.05
            text = '我一直在想词语' if index % 2 == 0 else '我昨天真的差点为此发推后来忍住了'
            segments.append(segment(text, start, start + 0.04))
        report = common.assess_transcript(transcript(segments, 70), 70)
        codes = {item['code'] for item in report['violations']}
        self.assertFalse(report['ok'])
        self.assertIn('alternating_repetition', codes)

    def test_four_identical_long_segments_are_rejected(self):
        segments = [segment('这是足够长的重复句子', index * 10, index * 10 + 5)
                    for index in range(4)]
        report = common.assess_transcript(transcript(segments, 40), 40)
        self.assertIn('consecutive_repetition', {item['code'] for item in report['violations']})

    def test_repetition_inside_one_segment_is_rejected(self):
        text = '这是完全虚构的事实' * 100
        report = common.assess_transcript(
            transcript([segment(text, 0, 119)], 120), 120)
        self.assertIn('intra_segment_repetition',
                      {item['code'] for item in report['violations']})

    def test_short_acknowledgements_do_not_trigger_repeat_gate(self):
        segments = [segment('对', index * 10, index * 10 + 1) for index in range(4)]
        segments.append(segment('接下来继续正常讨论主题', 41, 59))
        report = common.assess_transcript(transcript(segments, 60), 60)
        self.assertNotIn('consecutive_repetition', {item['code'] for item in report['violations']})

    def test_invalid_timestamps_and_long_gap_are_rejected(self):
        segments = [segment('开场正常内容', 0, 5), segment('结尾正常内容', 80, 90)]
        report = common.assess_transcript(transcript(segments, 100), 100)
        self.assertIn('long_internal_gap', {item['code'] for item in report['violations']})
        segments[1]['start'] = -1
        report = common.assess_transcript(transcript(segments, 100), 100)
        self.assertIn('invalid_segment_timestamp', {item['code'] for item in report['violations']})

    def test_segment_end_time_must_not_move_backwards(self):
        segments = [segment('覆盖很长范围的第一段', 0, 119),
                    segment('异常嵌套的第二段', 100, 101),
                    segment('恢复向后的第三段', 102, 119)]
        report = common.assess_transcript(transcript(segments, 120), 120)
        self.assertIn('non_monotonic_segments',
                      {item['code'] for item in report['violations']})

    def test_hf_hub_cache_takes_priority(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(common.os.environ, {'HF_HUB_CACHE': directory}, clear=False):
                self.assertEqual(common.huggingface_hub_roots()[0], directory)

    def test_empty_transcript_is_rejected(self):
        report = common.assess_transcript({'text': '', 'segments': []}, 60)
        codes = {item['code'] for item in report['violations']}
        self.assertIn('empty_segments', codes)
        self.assertIn('empty_transcript', codes)

    def test_short_video_with_large_missing_tail_is_rejected(self):
        data = transcript([segment('只有开头的一小段内容', 0, 1)], 119)
        report = common.assess_transcript(data, 119)
        self.assertIn('long_edge_gap', {item['code'] for item in report['violations']})

    def test_repeated_segments_without_timestamps_fail_without_crashing(self):
        segments = [{'text': '这是足够长的重复句子', 'words': []} for _ in range(4)]
        report = common.assess_transcript({'text': '', 'segments': segments}, 60)
        codes = {item['code'] for item in report['violations']}
        self.assertIn('invalid_segment_timestamp', codes)
        self.assertIn('consecutive_repetition', codes)

    def test_empty_segments_do_not_fake_time_coverage(self):
        segments = [segment('唯一真实内容', 0, 10)]
        for start in range(50, 1000, 50):
            segments.append({'text': '', 'start': start, 'end': start + 1, 'words': []})
        report = common.assess_transcript(transcript(segments, 1000), 1000)
        self.assertIn('long_edge_gap', {item['code'] for item in report['violations']})

    def test_empty_segments_do_not_dilute_dominant_repetition(self):
        segments = []
        for index in range(8):
            start = index * 20
            segments.append(segment('这是反复出现的幻觉文本', start, start + 5))
            segments.append({'text': '', 'start': start + 5, 'end': start + 10, 'words': []})
        for index in range(17):
            start = 170 + index * 10
            segments.append({'text': '', 'start': start, 'end': start + 5, 'words': []})
        report = common.assess_transcript(transcript(segments, 360), 360)
        self.assertIn('dominant_repetition', {item['code'] for item in report['violations']})

    def test_replacement_discards_invalid_timestamp_segments(self):
        invalid = [{'text': '坏时间戳', 'start': None, 'end': None}]
        replacement = [segment('修复内容', 0, 2)]
        result = common._replace_intervals(invalid, replacement, [(0, 2)])
        self.assertEqual(result, replacement)


class WhisperContractTests(unittest.TestCase):
    def test_transcribe_clip_uses_antiloop_parameters(self):
        fake = types.SimpleNamespace(transcribe=mock.Mock(return_value={'segments': [], 'text': ''}))
        with mock.patch.dict(sys.modules, {'mlx_whisper': fake}), \
                mock.patch.object(common, 'model_cache_dir', return_value='/model'):
            common.transcribe_clip('/audio.flac', 'zh')
        kwargs = fake.transcribe.call_args.kwargs
        self.assertFalse(kwargs['condition_on_previous_text'])
        self.assertTrue(kwargs['word_timestamps'])
        self.assertEqual(kwargs['hallucination_silence_threshold'], 2.0)
        self.assertEqual(kwargs['temperature'], (0.0, 0.2, 0.4, 0.6, 0.8, 1.0))
        self.assertEqual(kwargs['path_or_hf_repo'], '/model')

    def test_failed_quality_does_not_emit_result_or_files(self):
        bad = {'text': '坏结果', 'segments': [segment('这是足够长的重复句子', 0, 1)] * 4,
               'quality': {'ok': False, 'policy_version': 1, 'violations': []},
               'out_dir': '/unused'}
        output = io.StringIO()
        with redirect_stdout(output):
            with self.assertRaises(common.ASRQualityError):
                common.chunk_and_emit('id', 'title', bad, 'zh', '/unused')
        self.assertNotIn('RESULT_JSON:', output.getvalue())

    def test_success_result_contains_quality_contract(self):
        with tempfile.TemporaryDirectory() as directory:
            data = transcript([segment('一段足够正常的内容', 0, 10)], 10)
            data.update({'quality': {'ok': True, 'policy_version': 1, 'violations': []},
                         'repair_rounds': 0})
            output = io.StringIO()
            with redirect_stdout(output):
                payload = common.chunk_and_emit('id', '标题', data, 'zh', directory)
            self.assertTrue(payload['ok'])
            self.assertTrue(payload['quality']['ok'])
            self.assertIn('RESULT_JSON:', output.getvalue())
            self.assertTrue((Path(directory) / '逐字稿.md').is_file())
            self.assertTrue((Path(directory) / 'asr_quality.json').is_file())

    def test_transcribe_repairs_only_failed_interval(self):
        bad_segments = [segment('这是足够长的重复句子', index, index + 0.5)
                        for index in range(4)]
        repaired_segments = [segment('修复后得到不同而且正常的内容', 0, 10)]
        reports = [
            {'ok': False, 'policy_version': 1, 'metrics': {}, 'violations': [
                {'code': 'consecutive_repetition', 'start': 10, 'end': 20,
                 'observed': 4, 'limit': 3},
            ]},
            {'ok': True, 'policy_version': 1, 'metrics': {}, 'violations': []},
        ]
        with mock.patch.object(common, 'probe_audio_duration', return_value=100), \
                mock.patch.object(common, '_recognize_intervals',
                                  side_effect=[(bad_segments, [{'start': 0, 'end': 100}]),
                                               (repaired_segments, [{'start': 8, 'end': 22}])]) as recognize, \
                mock.patch.object(common, 'assess_transcript', side_effect=reports):
            result = common.transcribe('/audio.m4a', 'zh')
        self.assertEqual(result['repair_rounds'], 1)
        repair_intervals = recognize.call_args_list[1].args[2]
        self.assertEqual(repair_intervals, [(8, 22)])

    def test_transcribe_fails_closed_after_repair(self):
        bad_segments = [segment('这是足够长的重复句子', index, index + 0.5)
                        for index in range(4)]
        failed = {'ok': False, 'policy_version': 1, 'metrics': {}, 'violations': [
            {'code': 'consecutive_repetition', 'start': 10, 'end': 20,
             'observed': 4, 'limit': 3},
        ]}
        with mock.patch.object(common, 'probe_audio_duration', return_value=100), \
                mock.patch.object(common, '_recognize_intervals',
                                  return_value=(bad_segments, [])), \
                mock.patch.object(common, 'assess_transcript', side_effect=[failed, failed]):
            with self.assertRaises(common.ASRQualityError):
                common.transcribe('/audio.m4a', 'zh')

    def test_summary_placeholder_publish_never_overwrites_existing_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            summary = Path(directory) / '总结稿.md'
            summary.write_text('# 已完成总结\n\n' + ('完整中文正文' * 50), encoding='utf-8')
            before = summary.read_bytes()
            data = transcript([segment('一段足够正常的内容', 0, 10)], 10)
            data.update({'quality': {'ok': True, 'policy_version': 1, 'violations': []},
                         'repair_rounds': 0})
            with redirect_stdout(io.StringIO()):
                common.chunk_and_emit('id', '标题', data, 'zh', directory)
            self.assertEqual(summary.read_bytes(), before)

    def test_delivery_commit_rolls_back_when_interrupted(self):
        with tempfile.TemporaryDirectory() as directory:
            old_files = {
                'asr_transcript.json': 'old transcript',
                'asr_quality.json': 'old quality',
                '逐字稿.md': 'old verbatim',
                'id_chunk_0.txt': 'old chunk',
            }
            for name, content in old_files.items():
                (Path(directory) / name).write_text(content, encoding='utf-8')
            data = transcript([segment('一段足够正常的内容', 0, 10)], 10)
            data.update({'quality': {'ok': True, 'policy_version': 1, 'violations': []},
                         'repair_rounds': 0})
            real_replace = common.os.replace
            calls = {'count': 0}

            def interrupted_replace(source, destination):
                calls['count'] += 1
                if calls['count'] == 4:
                    raise KeyboardInterrupt()
                return real_replace(source, destination)

            with mock.patch.object(common.os, 'replace', side_effect=interrupted_replace):
                with self.assertRaises(KeyboardInterrupt):
                    common.chunk_and_emit('id', '标题', data, 'zh', directory)
            for name, content in old_files.items():
                self.assertEqual((Path(directory) / name).read_text(encoding='utf-8'), content)

    def test_delivery_commit_rolls_back_when_replace_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            old_files = {
                'asr_transcript.json': 'old transcript',
                'asr_quality.json': 'old quality',
                '逐字稿.md': 'old verbatim',
                'id_chunk_0.txt': 'old chunk',
            }
            for name, content in old_files.items():
                (Path(directory) / name).write_text(content, encoding='utf-8')
            data = transcript([segment('一段足够正常的内容', 0, 10)], 10)
            data.update({'quality': {'ok': True, 'policy_version': 1, 'violations': []},
                         'repair_rounds': 0})
            real_replace = common.os.replace
            calls = {'count': 0}

            def flaky_replace(source, destination):
                calls['count'] += 1
                if calls['count'] == 4:
                    raise OSError('injected replace failure')
                return real_replace(source, destination)

            with mock.patch.object(common.os, 'replace', side_effect=flaky_replace):
                with self.assertRaises(OSError):
                    common.chunk_and_emit('id', '标题', data, 'zh', directory)
            for name, content in old_files.items():
                self.assertEqual((Path(directory) / name).read_text(encoding='utf-8'), content)


if __name__ == '__main__':
    unittest.main()
