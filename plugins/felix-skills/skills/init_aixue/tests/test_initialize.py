"""初始化工具只在临时仓库生成文件，验证预览、幂等及保留。"""

import importlib.util
import json
import os
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('initialize', ROOT / 'scripts/initialize.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class InitializeTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = self.repo('control')

    def repo(self, name):
        repo = self.root / name
        repo.mkdir()
        subprocess.run(['git', 'init', '-q', str(repo)], check=True, capture_output=True)
        return repo

    def config(self):
        child = self.repo('api')
        return {'version': 1, 'coordinator': {'name': 'control'},
                'development_branch': 'dev/testing',
                'projects': [{'name': 'api', 'path': str(child)}]}

    def test_preview_does_not_write(self):
        result = module.initialize(self.target, 'standalone')
        self.assertFalse(result['applied'])
        self.assertFalse((self.target / 'docs').exists())

    def test_standalone_apply_is_idempotent_and_complete(self):
        module.initialize(self.target, 'standalone', apply=True)
        expected = ['environment', 'platform-operations', 'backend-service-release',
                    'mia-agent-release', 'mia-rpc-tool-host-release', 'human-handoff']
        for name in expected:
            self.assertTrue((self.target / f'docs/development/{name}.md').is_file())
        result = module.initialize(self.target, 'standalone', apply=True)
        self.assertTrue(all(item['state'] == 'unchanged' for item in result['files']))
        self.assertFalse((self.target / 'projects.json').exists())
        self.assertFalse((self.target / 'scripts').exists())

    def test_preserves_custom_files_and_existing_agent_source(self):
        (self.target / 'README.md').write_text('自定义内容\n')
        (self.target / 'CLAUDE.md').write_text('现有规则\n')
        (self.target / 'AGENTS.md').symlink_to('CLAUDE.md')
        module.initialize(self.target, 'standalone', apply=True)
        self.assertEqual((self.target / 'README.md').read_text(), '自定义内容\n')
        self.assertEqual((self.target / 'CLAUDE.md').read_text(), '现有规则\n')
        self.assertTrue((self.target / 'AGENTS.md').is_symlink())

    def test_custom_branch_adapts_all_development_docs(self):
        module.initialize(self.target, 'standalone', branch='dev/testing', apply=True)
        for path in (self.target / 'docs/development').glob('*.md'):
            self.assertNotIn('codex/dev', path.read_text())

    def test_centralized_installs_assets_without_runtime_binding(self):
        config = self.config()
        module.initialize(self.target, 'centralized', config=config, apply=True)
        self.assertEqual(json.loads((self.target/'projects.json').read_text()), config)
        self.assertTrue(os.access(self.target/'scripts/workspace', os.X_OK))
        self.assertTrue((self.target/'tests/test_workspace.py').is_file())
        self.assertFalse((self.target/'projects.local.json').exists())
        self.assertFalse((self.target/'.workspaces').exists())
        self.assertFalse((self.root/'api/AGENTS.md').exists())

    def test_existing_project_config_is_used_without_overwrite(self):
        config = self.config()
        (self.target/'projects.json').write_text(json.dumps(config))
        result = module.initialize(self.target, 'centralized', apply=True)
        self.assertEqual(result['branch'], 'dev/testing')
        self.assertEqual(json.loads((self.target/'projects.json').read_text()), config)
        second = module.initialize(self.target, 'centralized', apply=True)
        self.assertFalse(any(item['state'] == 'create' for item in second['files']))

    def test_tracked_runtime_binding_stops_before_writes(self):
        config = self.config()
        (self.target/'projects.local.json').write_text('{}')
        subprocess.run(['git', '-C', str(self.target), 'add', 'projects.local.json'], check=True)
        with self.assertRaises(ValueError):
            module.initialize(self.target, 'centralized', config=config, apply=True)
        self.assertFalse((self.target/'docs').exists())

    def test_invalid_config_writes_nothing(self):
        with self.assertRaises(ValueError):
            module.initialize(self.target, 'centralized', config={'version': 1}, apply=True)
        self.assertFalse((self.target/'docs').exists())

    def test_existing_ignore_rule_is_preserved_and_flagged(self):
        config = self.config()
        (self.target/'.gitignore').write_text('*.log\n')
        result = module.initialize(self.target, 'centralized', config=config, apply=True)
        self.assertEqual((self.target/'.gitignore').read_text(), '*.log\n')
        self.assertIn('需增量合并 /projects.local.json 忽略规则', result['notes'])

    def test_cli_requires_explicit_apply(self):
        result = subprocess.run([sys.executable, '-I', str(ROOT/'scripts/initialize.py'),
                                 '--target', str(self.target), '--mode', 'standalone'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)['applied'])
        self.assertFalse((self.target/'README.md').exists())

    def test_branch_conflict_writes_nothing(self):
        config = self.config()
        with self.assertRaises(ValueError):
            module.initialize(self.target, 'centralized', branch='other', config=config, apply=True)
        self.assertFalse((self.target/'docs').exists())

    def test_parent_file_conflict_is_detected_before_any_write(self):
        (self.target/'docs').mkdir()
        (self.target/'docs/agents').write_text('保留文件')
        with self.assertRaises(ValueError):
            module.initialize(self.target, 'standalone', apply=True)
        self.assertFalse((self.target/'docs/development').exists())
        self.assertEqual((self.target/'docs/agents').read_text(), '保留文件')

    def test_rejects_outside_symlink(self):
        outside = self.root/'outside'
        outside.mkdir()
        (self.target/'docs').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            module.initialize(self.target, 'standalone', apply=True)
        self.assertEqual(list(outside.iterdir()), [])

    def test_generated_links_and_handoff_scenarios_in_both_modes(self):
        config = self.config()
        for mode in ('standalone', 'centralized'):
            target = self.repo(mode)
            module.initialize(target, mode, config=config if mode == 'centralized' else None, apply=True)
            for file in target.rglob('*.md'):
                for dest in re.findall(r'\[[^\]]*\]\(([^)]+)\)', file.read_text()):
                    if '://' in dest:
                        continue
                    path, _, anchor = dest.partition('#')
                    linked = (file.parent / path).resolve() if path else file
                    self.assertTrue(linked.is_file(), f'{mode}: {file.name}: {dest}')
                    if anchor:
                        headings = re.findall(r'^#{1,6}\s+(.+)$', linked.read_text(), re.M)
                        slugs = [re.sub(r'[^\w\-\s]', '', h.replace('`', '').lower()).replace(' ', '-') for h in headings]
                        self.assertIn(anchor, slugs, dest)
            handoff = (target/'docs/development/human-handoff.md').read_text()
            for heading in ('创建 MIA RPC Version', '刷新 Workflow RPC snapshot', '发布 Skill 固定版本',
                            '创建或升级 Tool 固定版本', '创建 Host 后继草稿', '真实 HTTP 入口身份验收', '登录审批与权限协助'):
                self.assertIn('## ' + heading, handoff)

    def test_standalone_rejects_project_config(self):
        with self.assertRaises(ValueError):
            module.initialize(self.target, 'standalone', config=self.config(), apply=True)
        self.assertFalse((self.target/'docs').exists())

    def test_invalid_branch_type_is_rejected(self):
        config = self.config()
        config['development_branch'] = ['invalid']
        with self.assertRaises(ValueError):
            module.initialize(self.target, 'centralized', config=config, apply=True)
        self.assertFalse((self.target/'docs').exists())

    def test_duplicate_repository_rejected(self):
        config = {'version': 1, 'coordinator': {'name': 'control'},
                  'projects': [{'name': 'api', 'path': str(self.target)}]}
        with self.assertRaises(ValueError):
            module.initialize(self.target, 'centralized', config=config, apply=True)


if __name__ == '__main__':
    unittest.main()
