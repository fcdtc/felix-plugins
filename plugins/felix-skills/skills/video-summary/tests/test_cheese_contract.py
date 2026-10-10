import importlib.util
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / 'scripts' / 'bilibili_cheese_downloader.py'


def load_module():
    bilibili_api = types.ModuleType('bilibili_api')
    bilibili_api.Credential = object
    bilibili_api.cheese = object
    bilibili_api.login_v2 = object
    qrcode = types.ModuleType('qrcode')
    qrcode.QRCode = object
    with mock.patch.dict(sys.modules, {'bilibili_api': bilibili_api, 'qrcode': qrcode}):
        spec = importlib.util.spec_from_file_location('cheese_downloader', SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


class CheeseContractTests(unittest.TestCase):
    def test_ep_result_matches_delivery_contract(self):
        module = load_module()
        subtitle = {'body': [{'content': '第一句'}, {'content': '第二句'}]}
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.dict(module.os.environ, {'BILI_OUTPUT_DIR': directory}):
            result = module.save_subtitle_chunks(subtitle, 'ep123', '课程标题')
            self.assertTrue(result['ok'])
            self.assertEqual(result['schema_version'], '1.0')
            self.assertTrue(Path(result['deliverables']['verbatim']).is_file())
            self.assertTrue(Path(result['deliverables']['summary']).is_file())
            directory_name = Path(result['deliverables']['verbatim']).parent.name
            self.assertIn('课程标题', directory_name)
            self.assertTrue(all(Path(path).is_file() for path in result['chunks']))


if __name__ == '__main__':
    unittest.main()
