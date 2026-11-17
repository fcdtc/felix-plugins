"""报告CLI与packet引用校验桥接；只用匿名合成文件，无网络/模型。"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'skills/mia-eval/scripts/render_report.py'
SPEC = importlib.util.spec_from_file_location('mia_eval_citation_render', SCRIPT)
renderer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(renderer)


class CitationBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.manifest, reports, _ = renderer.demo_data()
        self.report = reports['pass']
        self.write('manifest.json', self.manifest)

    def write(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        return path

    def fixture(self, value, locator, source='input'):
        path = self.write('input.json', value)
        self.write('index.json', {'sources': {'input': {
            'path': str(path), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'root_type': 'array' if isinstance(value, list) else 'object'}}})
        self.report['evidence_index'][0].update(source=source, locator=locator)
        self.write('report.json', self.report)

    def command(self):
        return subprocess.run([sys.executable, '-I', '-B', str(SCRIPT), 'case',
                               '--manifest', str(self.root / 'manifest.json'),
                               '--report', str(self.root / 'report.json'),
                               '--packet', str(self.root / 'index.json'),
                               '--out', str(self.root / 'cases/pass.html')],
                              cwd=self.root, capture_output=True, text=True)

    def test_root_locator_valid_for_all_report_versions(self):
        for version in (1, 2, 3):
            manifest, reports, _ = renderer.demo_data(version)
            reports['pass']['evidence_index'][0]['locator'] = ''
            renderer.validate_case(reports['pass'], manifest)

    def test_cli_accepts_object_root_and_does_not_load_planted_module(self):
        self.fixture({'user_input': '匿名请求'}, '')
        (self.root / 'json.py').write_text('raise RuntimeError("不应加载下载目录模块")', encoding='utf-8')
        result = self.command()
        self.assertEqual(result.returncode, 0, result.stderr)
        verification = json.loads(result.stdout)['citation_verification']
        self.assertEqual(verification['checked_count'], 1)
        self.assertTrue(verification['complete'])
        self.assertEqual(verification['checked'][0]['locator'], [''])
        self.assertTrue((self.root / 'cases/pass.html').is_file())

    def test_cli_resolves_array_input_and_nested_json_pointer(self):
        self.fixture([{'payload': json.dumps({'a/b~': '匿名输入'})}], '["/0/payload", "/a~1b~0"]')
        result = self.command()
        self.assertEqual(result.returncode, 0, result.stderr)
        checked = json.loads(result.stdout)['citation_verification']['checked'][0]
        self.assertEqual(checked['type'], 'string')

    def test_cli_rejects_bad_pointer_before_writing_html(self):
        self.fixture([{'user_input': '匿名请求'}], '/user_input')
        result = self.command()
        self.assertEqual(result.returncode, 2)
        self.assertIn('引用校验失败', result.stderr)
        self.assertNotIn('Traceback', result.stderr)
        self.assertFalse((self.root / 'cases/pass.html').exists())

    def test_cli_rejects_source_hash_change_before_writing_html(self):
        self.fixture({'user_input': '匿名请求'}, '/user_input')
        self.write('input.json', {'user_input': '更新后的匿名请求'})
        result = self.command()
        self.assertEqual(result.returncode, 2)
        self.assertIn('哈希变化', result.stderr)
        self.assertFalse((self.root / 'cases/pass.html').exists())

    def test_cli_reports_unregistered_rule_source_as_unchecked(self):
        self.fixture({}, 'L1', source='unregistered-rule.md')
        result = self.command()
        self.assertEqual(result.returncode, 0, result.stderr)
        verification = json.loads(result.stdout)['citation_verification']
        self.assertEqual(verification['checked_count'], 0)
        self.assertFalse(verification['complete'])
        self.assertEqual(verification['unchecked'][0]['source'], 'unregistered-rule.md')


if __name__ == '__main__':
    unittest.main()
