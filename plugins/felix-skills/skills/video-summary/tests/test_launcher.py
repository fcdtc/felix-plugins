import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

SCRIPT = Path(__file__).parents[1] / 'scripts' / 'launcher.py'
spec = importlib.util.spec_from_file_location('video_summary_launcher', SCRIPT)
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


class LauncherTests(unittest.TestCase):
    def test_cache_root_prefers_xdg(self):
        root = launcher.cache_root({'HOME': '/home/test', 'XDG_CACHE_HOME': '/cache'})
        self.assertEqual(root, Path('/cache/felix-skills/video-summary'))

    def test_runtime_hash_changes_with_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            lock = Path(directory) / 'runtime.lock'
            lock.write_text('requests==1\n', encoding='utf-8')
            first = launcher.runtime_identity(lock)[0]
            lock.write_text('requests==2\n', encoding='utf-8')
            second = launcher.runtime_identity(lock)[0]
            self.assertNotEqual(first, second)

    def test_ready_environment_fast_path_does_not_build(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime_hash, fields = launcher.runtime_identity()
            final = root / 'venvs' / runtime_hash
            python = launcher.venv_python(final)
            python.parent.mkdir(parents=True)
            python.write_text('', encoding='utf-8')
            (final / '.ready.json').write_text(
                json.dumps(launcher.ready_payload(runtime_hash, fields)), encoding='utf-8')
            with mock.patch.object(launcher, 'environment_smoke', return_value=True), \
                    mock.patch.object(launcher, 'build_environment') as build:
                result = launcher.ensure_environment(root)
            self.assertEqual(result, final)
            build.assert_not_called()

    def test_unknown_action_is_rejected(self):
        with self.assertRaises(SystemExit):
            launcher.main(['arbitrary-script'])


if __name__ == '__main__':
    unittest.main()
