import importlib.util
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / 'scripts' / 'validate_delivery.py'
spec = importlib.util.spec_from_file_location('validate_delivery', SCRIPT)
validator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(validator)


class DeliveryValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.verbatim = self.root / '逐字稿.md'
        self.summary = self.root / '总结稿.md'
        self.verbatim.write_text('# Transcript\n\nThis is a complete English transcript.\n', encoding='utf-8')

    def tearDown(self):
        self.temp.cleanup()

    def write_summary(self, body):
        self.summary.write_text('# 内容总结\n\n' + body, encoding='utf-8')

    def test_english_transcript_and_chinese_summary_pass(self):
        self.write_summary('这是一份完整的中文总结。' * 30)
        result = validator.validate(self.verbatim, self.summary)
        self.assertTrue(result['ok'])

    def test_placeholder_summary_fails(self):
        self.write_summary(validator.PLACEHOLDER)
        result = validator.validate(self.verbatim, self.summary)
        self.assertEqual(result['error']['code'], 'summary_placeholder_present')

    def test_title_only_summary_fails(self):
        self.write_summary('')
        result = validator.validate(self.verbatim, self.summary)
        self.assertEqual(result['error']['code'], 'summary_too_short')

    def test_many_subheadings_without_body_fail(self):
        self.write_summary('\n'.join('## ' + ('标题' * 20) for _ in range(10)))
        result = validator.validate(self.verbatim, self.summary)
        self.assertEqual(result['error']['code'], 'summary_too_short')

    def test_english_summary_fails(self):
        self.write_summary('This is a long English summary. ' * 30)
        result = validator.validate(self.verbatim, self.summary)
        self.assertEqual(result['error']['code'], 'summary_not_chinese')

    def test_token_chinese_appendage_does_not_make_english_summary_pass(self):
        self.write_summary(('This remains an English summary with technical details. ' * 8)
                           + ('中文' * 10))
        result = validator.validate(self.verbatim, self.summary)
        self.assertEqual(result['error']['code'], 'summary_not_chinese')

    def test_chinese_summary_with_many_reference_urls_passes(self):
        body = ('这是一份完整的中文总结，说明主要结论、实现步骤和验证结果。' * 10)
        body += '\n' + '\n'.join(f'- https://example.com/reference/{index}' for index in range(20))
        self.write_summary(body)
        result = validator.validate(self.verbatim, self.summary)
        self.assertTrue(result['ok'])

    def test_missing_file_fails(self):
        result = validator.validate(self.verbatim, self.root / 'missing.md')
        self.assertEqual(result['error']['code'], 'missing_summary')

    def test_duplicate_paths_fail(self):
        result = validator.validate(self.verbatim, self.verbatim)
        self.assertEqual(result['error']['code'], 'duplicate_delivery_path')

    def test_invalid_utf8_fails_without_exception(self):
        self.summary.write_bytes(b'\xff\xfe')
        result = validator.validate(self.verbatim, self.summary)
        self.assertEqual(result['error']['code'], 'invalid_summary_encoding')


if __name__ == '__main__':
    unittest.main()
