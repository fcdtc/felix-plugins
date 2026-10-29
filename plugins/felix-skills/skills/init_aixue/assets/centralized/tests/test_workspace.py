"""真实临时 Git 仓库验证配置驱动的多仓绑定、codex/dev 基线和错误拒绝。"""

import json
import importlib.util
from unittest import mock
import fcntl
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="三仓工作区测试-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.repos = {}
        self.env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
        self.env.pop("GIT_DIR", None)
        self.env.pop("GIT_WORK_TREE", None)
        for name in ("coordinator", "api", "worker", "trigger"):
            repo = self.root / "源仓 库" / name
            repo.mkdir(parents=True)
            self.repos[name] = repo
            self.git(repo, "init", "-b", "main")
            self.git(repo, "config", "user.name", "工作区测试")
            self.git(repo, "config", "user.email", "workspace@example.invalid")
            (repo / "tracked.txt").write_text("起始内容\n")
            (repo / "AGENTS.md").write_text("通过 projects.local.json 定位项目。\n")
            (repo / ".gitignore").write_text("/projects.local.json\n")
            if name == "coordinator":
                shutil.copytree(ROOT / "scripts", repo / "scripts", ignore=shutil.ignore_patterns("__pycache__"))
                (repo / "projects.json").write_text(json.dumps({
                    "version": 1,
                    "coordinator": {"name": "coordinator"},
                    "projects": [{"name": child, "path": str(repo.parent / child)}
                                 for child in ("api", "worker", "trigger")],
                }))
            self.git(repo, "add", ".")
            self.git(repo, "commit", "-m", "初始提交")
            self.git(repo, "branch", "codex/dev")
        self.workspaces = self.root / "工作 区"

    def git(self, repo, *args):
        return subprocess.check_output(["git", "-C", str(repo), *args], env=self.env,
                                       stderr=subprocess.PIPE, text=True).strip()

    def command(self, *args, coordinator=None, success=True):
        script = (coordinator or self.repos["coordinator"]) / "scripts/workspace.py"
        result = subprocess.run([sys.executable, "-I", str(script), *args], env=self.env,
                                cwd=self.root, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(result.returncode, 0 if success else 1, result.stdout + result.stderr)
        return result

    def create(self, name="ticket01", *extra, success=True):
        return self.command("create", name, "--root", str(self.workspaces), *extra, success=success)

    def manifest(self, name="ticket01"):
        return json.loads((self.workspaces / name / "workspace.json").read_text())

    def test_create_configured_independent_worktrees_and_execute_at_bound_path(self):
        for repo in self.repos.values():
            (repo / "tracked.txt").write_text("默认目录未提交改动\n")
        self.create()
        manifest = self.manifest()
        self.assertEqual(manifest["state"], "ready")
        for name, repo in self.repos.items():
            path = self.workspaces / "ticket01" / name
            self.assertEqual(self.git(path, "branch", "--show-current"), "codex/dev")
            self.assertEqual((path / "tracked.txt").read_text(), "起始内容\n")
            self.assertEqual(self.git(repo, "branch", "--show-current"), "main")
            self.assertEqual((repo / "tracked.txt").read_text(), "默认目录未提交改动\n")
        coordinator = self.workspaces / "ticket01/coordinator"
        self.assertEqual(self.git(coordinator, "status", "--porcelain"), "")
        self.command("check", coordinator=coordinator)
        result = self.command("path", "worker", coordinator=coordinator)
        self.assertEqual(result.stdout.strip(), str(self.workspaces / "ticket01/worker"))
        result = self.command("path", "trigger", coordinator=coordinator)
        self.assertEqual(result.stdout.strip(), str(self.workspaces / "ticket01/trigger"))
        result = self.command("path", "--workspace", "ticket01", "--root", str(self.workspaces), "api")
        self.assertEqual(result.stdout.strip(), str(self.workspaces / "ticket01/api"))
        result = self.command("exec", "api", "--", sys.executable, "-c",
                              "import os; print(os.getcwd())", coordinator=coordinator)
        self.assertEqual(result.stdout.strip(), str(self.workspaces / "ticket01/api"))

    def test_only_one_workspace_can_use_pipeline_branch_at_a_time(self):
        self.create("task_a")
        result = self.create("task_b", success=False)
        self.assertIn("同一开发分支同一时间只能绑定一个工作区", result.stderr)
        self.assertFalse((self.workspaces / "task_b").exists())

    def test_repeated_create_preserves_task_changes_and_original_base(self):
        self.create()
        manifest = self.manifest()
        task_repo = self.workspaces / "ticket01/api"
        (task_repo / "tracked.txt").write_text("任务内的新提交\n")
        self.git(task_repo, "commit", "-am", "任务提交")
        (self.repos["api"] / "tracked.txt").write_text("源仓前进\n")
        self.git(self.repos["api"], "commit", "-am", "源仓更新")
        self.create()
        self.assertEqual(self.manifest(), manifest)
        self.assertEqual((task_repo / "tracked.txt").read_text(), "任务内的新提交\n")

    def test_explicit_baselines_are_pinned(self):
        repo = self.repos["worker"]
        base = self.git(repo, "rev-parse", "HEAD")
        (repo / "tracked.txt").write_text("较新的版本\n")
        self.git(repo, "commit", "-am", "新版本")
        self.create("ticket01", "--base", "worker=codex/dev")
        self.assertEqual(self.git(self.workspaces / "ticket01/worker", "rev-parse", "HEAD"), base)
        self.create("ticket01", "--base", "worker=main", success=False)

    def test_branch_collision_rejected_before_any_worktree_creation(self):
        occupied = self.root / "occupied executor"
        self.git(self.repos["worker"], "worktree", "add", str(occupied), "codex/dev")
        self.create(success=False)
        self.assertFalse((self.workspaces / "ticket01").exists())
        self.assertEqual(self.git(self.repos["worker"], "worktree", "list", "--porcelain").count("worktree "), 2)

    def test_invalid_reference_rejected_before_creation(self):
        self.create("ticket01", "--base", "worker=missing-reference", success=False)
        self.assertFalse((self.workspaces / "ticket01").exists())

    def test_existing_foreign_directory_preserved(self):
        destination = self.workspaces / "ticket01"
        destination.mkdir(parents=True)
        (destination / "user.txt").write_text("已有文件\n")
        self.create(success=False)
        self.assertEqual((destination / "user.txt").read_text(), "已有文件\n")

    def test_partial_creation_resumes_without_resetting_existing_task(self):
        self.create()
        manifest = self.manifest()
        manifest["state"] = "preparing"
        (self.workspaces / "ticket01/workspace.json").write_text(json.dumps(manifest))
        (self.workspaces / "ticket01/coordinator/projects.local.json").unlink()
        self.git(self.repos["worker"], "worktree", "remove", str(self.workspaces / "ticket01/worker"))
        (self.workspaces / "ticket01/api/tracked.txt").write_text("保留部分执行后的修改\n")
        self.create()
        self.assertEqual(self.manifest()["state"], "ready")
        self.assertEqual((self.workspaces / "ticket01/api/tracked.txt").read_text(), "保留部分执行后的修改\n")

    def test_missing_binding_never_falls_back_to_default_repositories(self):
        self.command("path", "api", success=False)
        self.create()
        coordinator = self.workspaces / "ticket01/coordinator"
        (coordinator / "projects.local.json").unlink()
        self.command("exec", "api", "--", "git", "status", coordinator=coordinator, success=False)

    def test_newly_registered_project_requires_a_new_complete_workspace(self):
        projects = json.loads((self.repos["coordinator"] / "projects.json").read_text())
        trigger = projects["projects"].pop()
        (self.repos["coordinator"] / "projects.json").write_text(json.dumps(projects))
        self.git(self.repos["coordinator"], "commit", "-am", "三仓配置")
        self.git(self.repos["coordinator"], "branch", "-f", "codex/dev", "HEAD")
        self.create()
        coordinator = self.workspaces / "ticket01/coordinator"
        configured = json.loads((coordinator / "projects.json").read_text())
        configured["projects"].append(trigger)
        (coordinator / "projects.json").write_text(json.dumps(configured))
        result = self.command("create", "ticket02", "--root", str(self.workspaces),
                              coordinator=coordinator, success=False)
        self.assertIn("创建新的完整工作区", result.stderr)
        self.assertFalse((self.workspaces / "ticket02").exists())

        projects["projects"].append(trigger)
        (self.repos["coordinator"] / "projects.json").write_text(json.dumps(projects))
        self.git(self.repos["coordinator"], "commit", "-am", "恢复完整项目配置")
        result = self.create(success=False)
        self.assertIn("创建新的完整工作区", result.stderr)

    def test_workspace_commands_reject_project_set_drift(self):
        self.create()
        coordinator = self.workspaces / "ticket01/coordinator"
        configured = json.loads((coordinator / "projects.json").read_text())
        configured["projects"].pop()
        (coordinator / "projects.json").write_text(json.dumps(configured))
        for action in (("check",), ("path", "api"),
                       ("exec", "api", "--", "git", "status")):
            result = self.command(*action, coordinator=coordinator, success=False)
            self.assertIn("项目集合", result.stderr)

    def test_uncommitted_project_config_is_rejected_before_creation(self):
        configured = json.loads((self.repos["coordinator"] / "projects.json").read_text())
        configured["projects"].pop()
        (self.repos["coordinator"] / "projects.json").write_text(json.dumps(configured))
        result = self.create(success=False)
        self.assertIn("尚未提交", result.stderr)
        self.assertFalse((self.workspaces / "ticket01").exists())

    def test_coordinator_baseline_project_config_must_match_current_config(self):
        original = self.git(self.repos["coordinator"], "rev-parse", "HEAD")
        configured = json.loads((self.repos["coordinator"] / "projects.json").read_text())
        configured["projects"].pop()
        (self.repos["coordinator"] / "projects.json").write_text(json.dumps(configured))
        self.git(self.repos["coordinator"], "commit", "-am", "移除项目")
        result = self.create("ticket01", "--base", "coordinator=codex/dev", success=False)
        self.assertIn("协调仓基线", result.stderr)

    def test_project_config_rejects_invalid_schema_and_case_collisions(self):
        config_file = self.repos["coordinator"] / "projects.json"
        configured = json.loads(config_file.read_text())
        configured.pop("version")
        config_file.write_text(json.dumps(configured))
        result = self.create(success=False)
        self.assertIn("格式无效", result.stderr)

        configured["version"] = 1
        configured["projects"].append({"name": "TRIGGER", "path": "/unused"})
        config_file.write_text(json.dumps(configured))
        result = self.create(success=False)
        self.assertIn("大小写", result.stderr)

    def test_unknown_project_is_rejected_after_workspace_validation(self):
        self.create()
        coordinator = self.workspaces / "ticket01/coordinator"
        result = self.command("path", "unknown", coordinator=coordinator, success=False)
        self.assertIn("项目未在当前工作区登记", result.stderr)

    def test_wrong_branch_rejected_without_running_command(self):
        self.create()
        target = self.workspaces / "ticket01/worker"
        self.git(target, "switch", "-c", "unexpected-branch")
        self.command("exec", "worker", "--", sys.executable, "-c",
                     "open('must-not-exist','w').write('bad')",
                     coordinator=self.workspaces / "ticket01/coordinator", success=False)
        self.assertFalse((target / "must-not-exist").exists())

    def test_binding_tampering_rejected(self):
        self.create()
        coordinator = self.workspaces / "ticket01/coordinator"
        binding_file = coordinator / "projects.local.json"
        binding = json.loads(binding_file.read_text())
        binding["projects"][0]["path"] = str(self.repos["api"])
        binding_file.write_text(json.dumps(binding))
        self.command("check", coordinator=coordinator, success=False)
        self.create(success=False)

    def test_old_coordinator_baseline_rejected(self):
        repo = self.repos["coordinator"]
        (repo / "AGENTS.md").write_text("旧的默认目录定位规则\n")
        self.git(repo, "commit", "-am", "旧规则")
        self.git(repo, "branch", "-f", "codex/dev", "HEAD")
        self.create(success=False)
        self.assertFalse((self.workspaces / "ticket01").exists())

    def commit_agent_rules(self, repo):
        self.git(repo, "add", ".")
        self.git(repo, "commit", "-m", "代理入口调整")
        self.git(repo, "branch", "-f", "codex/dev", "HEAD")

    def test_claude_only_baseline_is_supported(self):
        repo = self.repos["coordinator"]
        (repo / "AGENTS.md").rename(repo / "CLAUDE.md")
        self.commit_agent_rules(repo)
        self.create()
        self.command("check", coordinator=self.workspaces / "ticket01/coordinator")

    def test_relative_agent_symlink_reads_committed_rule_source(self):
        repo = self.repos["coordinator"]
        (repo / "AGENTS.md").rename(repo / "CLAUDE.md")
        (repo / "AGENTS.md").symlink_to("CLAUDE.md")
        self.commit_agent_rules(repo)
        self.create()
        self.assertTrue((self.workspaces / "ticket01/coordinator/AGENTS.md").is_symlink())

    def test_agent_symlink_escape_rejected_before_creation(self):
        repo = self.repos["coordinator"]
        (repo / "AGENTS.md").unlink()
        (repo / "AGENTS.md").symlink_to("../outside.md")
        self.commit_agent_rules(repo)
        result = self.create(success=False)
        self.assertIn("越界", result.stderr)
        self.assertFalse((self.workspaces / "ticket01").exists())

    def test_agent_symlink_cycle_rejected_before_creation(self):
        repo = self.repos["coordinator"]
        (repo / "AGENTS.md").unlink()
        (repo / "AGENTS.md").symlink_to("CLAUDE.md")
        (repo / "CLAUDE.md").symlink_to("AGENTS.md")
        self.commit_agent_rules(repo)
        result = self.create(success=False)
        self.assertIn("成环", result.stderr)
        self.assertFalse((self.workspaces / "ticket01").exists())

    def test_unignored_binding_rejected_before_creation(self):
        repo = self.repos["coordinator"]
        (repo / ".gitignore").write_text("*.log\n")
        self.git(repo, "commit", "-am", "缺少忽略规则")
        self.git(repo, "branch", "-f", "codex/dev", "HEAD")
        self.create(success=False)
        self.assertFalse((self.workspaces / "ticket01").exists())

    def test_workspace_root_inside_checkout_rejected(self):
        self.command("create", "ticket01", "--root", str(self.repos["coordinator"] / ".workspaces"), success=False)

    def test_path_traversal_rejected(self):
        self.create("../escape", success=False)

    def test_execution_returns_command_exit_code(self):
        self.create()
        script = self.workspaces / "ticket01/coordinator/scripts/workspace.py"
        result = subprocess.run([sys.executable, str(script), "exec", "api", "--",
                                 sys.executable, "-c", "raise SystemExit(7)"], env=self.env,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(result.returncode, 7)

    def test_creation_lock_prevents_overlapping_create(self):
        self.workspaces.mkdir()
        with (self.workspaces / ".workspace.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = self.create(success=False)
            self.assertIn("正在创建工作区", result.stderr)
            self.assertFalse((self.workspaces / "ticket01").exists())

    def test_archived_source_worktree_does_not_break_binding(self):
        source = self.root / "临时协调检出"
        self.git(self.repos["coordinator"], "worktree", "add", "-b", "source-task", str(source))
        self.command("create", "ticket01", "--root", str(self.workspaces), coordinator=source)
        self.git(self.repos["coordinator"], "worktree", "remove", str(source))
        self.command("check", coordinator=self.workspaces / "ticket01/coordinator")

    def test_partial_foreign_child_directory_is_not_overwritten(self):
        self.create()
        manifest = self.manifest()
        manifest["state"] = "preparing"
        (self.workspaces / "ticket01/workspace.json").write_text(json.dumps(manifest))
        child = self.workspaces / "ticket01/worker"
        self.git(self.repos["worker"], "worktree", "remove", str(child))
        child.mkdir()
        (child / "user.txt").write_text("已有文件\n")
        self.create(success=False)
        self.assertEqual((child / "user.txt").read_text(), "已有文件\n")

    def update_config(self, **changes):
        repo = self.repos["coordinator"]
        path = repo / "projects.json"
        configured = json.loads(path.read_text())
        configured.update(changes)
        path.write_text(json.dumps(configured))
        self.git(repo, "commit", "-am", "更新配置")
        self.git(repo, "branch", "-f", "codex/dev", "HEAD")
        return configured

    def remove(self, success=True):
        # 从源协调仓执行；cwd 显式在临时根目录，绝不位于待删除 worktree。
        return self.command("remove", "ticket01", "--root", str(self.workspaces), success=success)

    def test_custom_coordinator_name_branch_and_single_child(self):
        self.update_config(coordinator={"name": "hub_01"}, development_branch="team/integration",
                           projects=[{"name": "api", "path": "../api"}])
        for repo in self.repos.values():
            self.git(repo, "branch", "team/integration")
        self.create("ticket01", "--branch", "team/integration", "--base", "api=refs/heads/team/integration")
        manifest = self.manifest()
        self.assertEqual(manifest["coordinator_name"], "hub_01")
        self.assertEqual(manifest["development_branch"], "team/integration")
        self.assertEqual(set(manifest["repositories"]), {"hub_01", "api"})
        hub = self.workspaces / "ticket01/hub_01"
        self.command("check", coordinator=hub)
        self.assertEqual(self.command("path", "api", coordinator=hub).stdout.strip(),
                         str(self.workspaces / "ticket01/api"))
        self.remove()

    def test_branch_argument_and_invalid_configuration_rejected(self):
        self.create("ticket01", "--branch", "main", success=False)
        repo = self.repos["coordinator"]
        path = repo / "projects.json"
        configured = json.loads(path.read_text())
        for invalid in (None, "", "HEAD", "-bad", "bad..branch", "bad branch"):
            with self.subTest(branch=invalid):
                configured["development_branch"] = invalid
                path.write_text(json.dumps(configured))
                self.create(success=False)
                self.assertFalse((self.workspaces / "ticket01").exists())

    def test_check_rejects_coordinator_role_change_with_same_project_set(self):
        self.create()
        hub = self.workspaces / "ticket01/coordinator"
        path = hub / "projects.json"
        configured = json.loads(path.read_text())
        configured["coordinator"]["name"] = "api"
        configured["projects"][0]["name"] = "coordinator"
        path.write_text(json.dumps(configured))
        for action in (("check",), ("path", "api"), ("exec", "api", "--", "git", "status")):
            result = self.command(*action, coordinator=hub, success=False)
            self.assertIn("协调仓策略", result.stderr)

    def test_check_and_repeated_create_reject_branch_policy_drift(self):
        self.create()
        hub = self.workspaces / "ticket01/coordinator"
        path = hub / "projects.json"
        configured = json.loads(path.read_text())
        configured["development_branch"] = "team/new"
        path.write_text(json.dumps(configured))
        result = self.command("check", coordinator=hub, success=False)
        self.assertIn("开发分支策略", result.stderr)
        result = self.command("create", "ticket01", "--root", str(self.workspaces),
                              coordinator=hub, success=False)
        self.assertIn("开发分支策略", result.stderr)

    def test_missing_development_branch_never_falls_back_to_head_or_tag(self):
        repo = self.repos["api"]
        self.git(repo, "branch", "-D", "codex/dev")
        self.git(repo, "tag", "codex/dev")
        self.create(success=False)
        self.assertFalse((self.workspaces / "ticket01").exists())
        self.create("ticket01", "--base", "api=codex/dev", success=False)
        self.assertFalse((self.workspaces / "ticket01").exists())

    def test_origin_development_branch_is_used_when_local_missing(self):
        for repo in self.repos.values():
            self.git(repo, "update-ref", "refs/remotes/origin/codex/dev", "HEAD")
            self.git(repo, "branch", "-D", "codex/dev")
        self.create("ticket01", "--base", "api=origin/codex/dev")
        for entry in self.manifest()["repositories"].values():
            self.assertEqual(entry["base_ref"], "refs/remotes/origin/codex/dev")
            self.assertEqual(self.git(Path(entry["path"]), "branch", "--show-current"), "codex/dev")

    def test_local_development_branch_preferred_over_origin(self):
        repo = self.repos["api"]
        local = self.git(repo, "rev-parse", "codex/dev")
        (repo / "tracked.txt").write_text("远程版本\n")
        self.git(repo, "commit", "-am", "远程更新")
        self.git(repo, "update-ref", "refs/remotes/origin/codex/dev", "HEAD")
        self.create()
        self.assertEqual(self.manifest()["repositories"]["api"]["base_commit"], local)

    def test_missing_binding_create_in_workspace_does_not_use_default_sources(self):
        self.create()
        hub = self.workspaces / "ticket01/coordinator"
        (hub / "projects.local.json").unlink()
        self.command("create", "ticket02", "--root", str(self.workspaces), coordinator=hub, success=False)
        self.assertFalse((self.workspaces / "ticket02").exists())

    def test_remove_backs_up_ignored_files_extra_files_and_preserves_commits(self):
        self.create()
        workspace = self.workspaces / "ticket01"
        api = workspace / "api"
        (api / ".gitignore").write_text("/cache/\n/link\n")
        (api / "tracked.txt").write_text("任务提交\n")
        self.git(api, "add", ".gitignore", "tracked.txt")
        self.git(api, "commit", "-m", "保留开发成果")
        commit = self.git(api, "rev-parse", "HEAD")
        (api / "cache").mkdir()
        (api / "cache/session.txt").write_text("忽略文件数据\n")
        outside = self.root / "不应删除.txt"
        outside.write_text("外部数据\n")
        (api / "link").symlink_to(outside)
        (workspace / "notes").mkdir()
        (workspace / "notes/user.txt").write_text("额外文件\n")
        result = self.remove()
        self.assertIn("本地 Git 分支保留", result.stdout)
        self.assertFalse(workspace.exists())
        archives = list((self.workspaces / ".workspace-archive").iterdir())
        self.assertEqual(len(archives), 1)
        archive = archives[0]
        self.assertEqual((archive / "api/cache/session.txt").read_text(), "忽略文件数据\n")
        self.assertEqual((archive / "_workspace/notes/user.txt").read_text(), "额外文件\n")
        self.assertTrue((archive / "api/link").is_symlink())
        self.assertEqual(outside.read_text(), "外部数据\n")
        self.assertTrue((archive / "coordinator/projects.local.json").is_file())
        self.assertEqual(self.git(self.repos["api"], "rev-parse", "codex/dev"), commit)
        for repo in self.repos.values():
            self.assertEqual(self.git(repo, "worktree", "list", "--porcelain").count("worktree "), 1)

    def test_remove_rejects_uncommitted_and_untracked_before_deleting_anything(self):
        self.create()
        workspace = self.workspaces / "ticket01"
        api = workspace / "api"
        (api / "tracked.txt").write_text("未提交\n")
        self.assertIn("未提交或未跟踪", self.remove(success=False).stderr)
        (api / "tracked.txt").write_text("起始内容\n")
        (api / "untracked.txt").write_text("未跟踪\n")
        self.assertIn("未提交或未跟踪", self.remove(success=False).stderr)
        for name in self.repos:
            self.assertTrue((workspace / name).is_dir())
        self.assertFalse((self.workspaces / ".workspace-archive").exists())

    def test_remove_uses_creation_lock_and_can_retry_after_release(self):
        self.create()
        with (self.workspaces / ".workspace.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertIn("正在创建工作区", self.remove(success=False).stderr)
            self.create("ticket02", success=False)
            self.assertTrue((self.workspaces / "ticket01/api").exists())
        self.remove()

    def test_remove_rejects_tampered_binding_and_path_escape(self):
        self.create()
        workspace = self.workspaces / "ticket01"
        binding_path = workspace / "coordinator/projects.local.json"
        original_binding = binding_path.read_text()
        binding = json.loads(original_binding)
        binding["projects"][0]["path"] = str(self.repos["api"])
        binding_path.write_text(json.dumps(binding))
        self.remove(success=False)
        binding_path.write_text(original_binding)
        manifest = self.manifest()
        manifest["repositories"]["api"]["path"] = str(self.repos["api"])
        (workspace / "workspace.json").write_text(json.dumps(manifest))
        self.assertIn("路径越界", self.remove(success=False).stderr)
        self.assertTrue((workspace / "api").exists())
        self.assertTrue((self.repos["api"] / "tracked.txt").exists())

    def test_remove_partial_failure_can_resume_without_removing_branches(self):
        self.create()
        workspace = self.workspaces / "ticket01"
        self.git(self.repos["api"], "worktree", "remove", str(workspace / "api"))
        self.remove()
        self.assertFalse(workspace.exists())
        self.assertTrue(self.git(self.repos["api"], "rev-parse", "codex/dev"))

    def test_manifest_requires_new_policy_fields_and_source_identity(self):
        self.create()
        path = self.workspaces / "ticket01/workspace.json"
        manifest = self.manifest()
        for field in ("coordinator_name", "development_branch"):
            changed = dict(manifest)
            changed.pop(field)
            path.write_text(json.dumps(changed))
            self.command("check", "ticket01", "--root", str(self.workspaces), success=False)
        manifest["repositories"]["api"]["source"] = str(self.repos["worker"] / ".git")
        path.write_text(json.dumps(manifest))
        self.command("check", "ticket01", "--root", str(self.workspaces), success=False)
        self.remove(success=False)

    def test_wrapper_uses_own_directory_and_preserves_exit_code(self):
        self.create()
        wrapper = self.workspaces / "ticket01/coordinator/scripts/workspace"
        result = subprocess.run([str(wrapper), "exec", "api", "--", sys.executable, "-c",
                                 "import os; print(os.getcwd()); raise SystemExit(9)"],
                                env=self.env, cwd=self.root, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(result.returncode, 9, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), str(self.workspaces / "ticket01/api"))
        result = subprocess.run([str(wrapper), "path", "unknown"], env=self.env, cwd=self.root,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(result.returncode, 1)

    def test_actual_partial_remove_failure_retains_manifest_and_retries(self):
        self.create()
        spec = importlib.util.spec_from_file_location("workspace_under_test", ROOT / "scripts/workspace.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        original_git = module.git
        removals = []

        def fail_second_remove(repo, *args, **kwargs):
            if args[:2] == ("worktree", "remove"):
                removals.append(args[-1])
                if len(removals) == 2:
                    raise module.WorkspaceError("模拟删除中断")
            return original_git(repo, *args, **kwargs)

        # 不调用 assets 的 main；只测试已复制临时协调仓的内部操作。
        with mock.patch.object(module, "COORDINATOR", self.repos["coordinator"]), \
                mock.patch.object(module, "git", side_effect=fail_second_remove), \
                mock.patch.dict(os.environ, self.env, clear=True):
            args = type("Args", (), {"name": "ticket01", "root": self.workspaces})()
            with self.assertRaisesRegex(module.WorkspaceError, "仅部分移除"):
                module.remove_workspace(args)
        self.assertTrue((self.workspaces / "ticket01/workspace.json").is_file())
        self.assertFalse((self.workspaces / "ticket01/coordinator").exists())
        self.assertTrue((self.workspaces / "ticket01/api").exists())
        self.remove()
        self.assertFalse((self.workspaces / "ticket01").exists())

    def test_symlinked_binding_manifest_and_archive_are_rejected(self):
        self.create()
        workspace = self.workspaces / "ticket01"
        for original in (workspace / "workspace.json", workspace / "coordinator/projects.local.json"):
            content = original.read_text()
            external = self.root / (original.name + ".external")
            external.write_text(content)
            original.unlink()
            original.symlink_to(external)
            self.remove(success=False)
            self.command("check", "ticket01", "--root", str(self.workspaces), success=False)
            original.unlink()
            original.write_text(content)
        external_archive = self.root / "external-archive"
        external_archive.mkdir()
        (self.workspaces / ".workspace-archive").symlink_to(external_archive, target_is_directory=True)
        self.assertIn("备份目录不能经过符号链接", self.remove(success=False).stderr)
        self.assertEqual(list(external_archive.iterdir()), [])
        self.assertTrue((workspace / "api").exists())

    def test_assets_execution_is_rejected_before_any_workspace_creation(self):
        for script in (ROOT / "scripts/workspace", ROOT / "scripts/workspace.py"):
            command = [str(script)] if script.suffix != ".py" else [sys.executable, "-I", str(script)]
            result = subprocess.run([*command, "create", "ticket01", "--root", str(self.workspaces)],
                                    env=self.env, cwd=self.root, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            self.assertEqual(result.returncode, 1)
            self.assertIn("禁止从 skill assets", result.stderr)
            self.assertFalse(self.workspaces.exists())


if __name__ == "__main__":
    unittest.main()
