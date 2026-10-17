#!/usr/bin/env python3
"""Validate the repository's Claude Code and Codex skill contracts."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = ROOT / "plugins" / "felix-skills"
SKILLS_ROOT = PLUGIN_ROOT / "skills"
ARCHIVE_ROOT = PLUGIN_ROOT / "archive" / "deprecated-skills"
CLAUDE_MANIFEST = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
CODEX_MANIFEST = PLUGIN_ROOT / ".codex-plugin" / "plugin.json"
COMPATIBILITY = PLUGIN_ROOT / "compatibility.json"
CLAUDE_ONLY = "claude-only"
CROSS_PLATFORM = "claude-and-codex"


class ValidationError(Exception):
    pass


def display_path(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ValidationError(f"missing file: {display_path(path)}") from exc
    except json.JSONDecodeError as exc:
        raise ValidationError(f"invalid JSON in {display_path(path)}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValidationError(f"expected a JSON object in {display_path(path)}")
    return value


def parse_frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValidationError(f"missing frontmatter: {display_path(path)}")
    parts = text.split("---\n", 2)
    if len(parts) != 3:
        raise ValidationError(f"unterminated frontmatter: {display_path(path)}")
    block = parts[1]

    values: dict[str, str] = {}
    for line in block.splitlines():
        if line.startswith((" ", "-")) or ":" not in line:
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def parse_openai_sidecar(path: Path) -> tuple[str, str, bool | None]:
    text = path.read_text(encoding="utf-8")
    match = re.fullmatch(
        r'interface:\n'
        r'  display_name: "([^"\n]+)"\n'
        r'  short_description: "([^"\n]+)"\n'
        r'(?:policy:\n'
        r'  allow_implicit_invocation: (true|false)\n?)?',
        text,
    )
    if not match:
        raise ValidationError(
            f"invalid Codex sidecar structure: {display_path(path)}"
        )
    implicit = None if match.group(3) is None else match.group(3) == "true"
    return match.group(1), match.group(2), implicit


def resolve_skill_root(raw_root: str, label: str) -> tuple[Path | None, list[str]]:
    errors: list[str] = []
    root = (PLUGIN_ROOT / raw_root).resolve()
    try:
        root.relative_to(PLUGIN_ROOT.resolve())
    except ValueError:
        return None, [f"{label} skill root escapes plugin directory: {raw_root}"]
    if not root.is_dir():
        errors.append(f"{label} skill root is not a directory: {raw_root}")
    return root, errors


def discover_claude_skill_dirs(plugin: dict) -> tuple[list[Path], list[str]]:
    errors: list[str] = []
    roots = [SKILLS_ROOT]
    configured_roots = plugin.get("skills", [])
    if not isinstance(configured_roots, list) or not all(
        isinstance(root, str) for root in configured_roots
    ):
        return [], ["Claude plugin skills must be a string list"]

    for raw_root in configured_roots:
        root, root_errors = resolve_skill_root(raw_root, "Claude plugin")
        errors.extend(root_errors)
        if root is not None and root.is_dir():
            roots.append(root)

    skill_dirs: list[Path] = []
    seen: set[Path] = set()
    for root in roots:
        for skill_file in sorted(root.glob("*/SKILL.md")):
            skill_dir = skill_file.parent.resolve()
            if skill_dir not in seen:
                seen.add(skill_dir)
                skill_dirs.append(skill_dir)
    return sorted(skill_dirs), errors


def discover_codex_skill_dirs(plugin: dict) -> tuple[list[Path], list[str]]:
    raw_root = plugin.get("skills")
    if not isinstance(raw_root, str):
        return [], ["Codex plugin skills must be a string"]
    if raw_root.rstrip("/") != "./skills":
        return [], ["Codex plugin skills must be ./skills/"]

    root, errors = resolve_skill_root(raw_root, "Codex plugin")
    if root is None or not root.is_dir():
        return [], errors
    return sorted(path.parent.resolve() for path in root.rglob("SKILL.md")), errors


def validate_markdown_links(path: Path) -> list[str]:
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    in_fence = False
    visible_lines: list[str] = []
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if not in_fence:
            visible_lines.append(line)

    for raw_target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", "\n".join(visible_lines)):
        if raw_target.startswith("<") and raw_target.endswith(">"):
            raw_target = raw_target[1:-1]
        parsed = urlsplit(raw_target)
        if parsed.scheme or parsed.netloc or not parsed.path:
            continue
        decoded = unquote(parsed.path)
        if decoded in {"link", "url"}:
            continue
        if not (path.parent / decoded).exists():
            errors.append(
                f"broken local link in {display_path(path)}: {raw_target}"
            )
    return errors


def validate() -> list[str]:
    errors: list[str] = []

    try:
        marketplace = load_json(ROOT / ".claude-plugin" / "marketplace.json")
        claude_plugin = load_json(CLAUDE_MANIFEST)
        codex_plugin = load_json(CODEX_MANIFEST)
        compatibility = load_json(COMPATIBILITY)
    except ValidationError as exc:
        return [str(exc)]

    if claude_plugin.get("name") != codex_plugin.get("name"):
        errors.append("Claude and Codex plugin names differ")
    if claude_plugin.get("version") != codex_plugin.get("version"):
        errors.append("Claude and Codex plugin versions differ")

    entries = marketplace.get("plugins", [])
    if len(entries) != 1:
        errors.append("marketplace must contain exactly one plugin")
    else:
        entry = entries[0]
        if entry.get("name") != claude_plugin.get("name"):
            errors.append("marketplace and plugin names differ")
        source = entry.get("source")
        expected_source = PLUGIN_ROOT.resolve()
        if not isinstance(source, str):
            errors.append("marketplace plugin source must be a string")
        else:
            resolved_source = (ROOT / source).resolve()
            if resolved_source != expected_source or not resolved_source.is_dir():
                errors.append("marketplace plugin source must be ./plugins/felix-skills")
            elif not (resolved_source / ".claude-plugin" / "plugin.json").is_file():
                errors.append("marketplace plugin source is missing Claude plugin.json")
            elif not (resolved_source / ".codex-plugin" / "plugin.json").is_file():
                errors.append("marketplace plugin source is missing Codex plugin.json")

    claude_skill_dirs, claude_discovery_errors = discover_claude_skill_dirs(
        claude_plugin
    )
    codex_skill_dirs, codex_discovery_errors = discover_codex_skill_dirs(codex_plugin)
    errors.extend(claude_discovery_errors)
    errors.extend(codex_discovery_errors)

    claude_names = {path.name for path in claude_skill_dirs}
    codex_names = {path.name for path in codex_skill_dirs}
    duplicate_codex_names = {
        path.name
        for path in codex_skill_dirs
        if sum(other.name == path.name for other in codex_skill_dirs) > 1
    }
    for name in sorted(duplicate_codex_names):
        errors.append(f"duplicate Codex skill directory name: {name}")
    if claude_names != codex_names:
        errors.append(
            "Claude and Codex skill inventories differ: "
            f"claude={sorted(claude_names)}, codex={sorted(codex_names)}"
        )

    skill_dirs = claude_skill_dirs
    actual_names: set[str] = set()
    duplicate_directory_names: set[str] = set()
    for skill_dir in skill_dirs:
        if skill_dir.name in actual_names:
            duplicate_directory_names.add(skill_dir.name)
        actual_names.add(skill_dir.name)
    for name in sorted(duplicate_directory_names):
        errors.append(f"duplicate skill directory name: {name}")

    archived_names = (
        {path.parent.name for path in ARCHIVE_ROOT.glob("*/SKILL.md")}
        if ARCHIVE_ROOT.is_dir()
        else set()
    )
    expected_archived_names = {"adhd", "deepinit", "ticket-loop"}
    if archived_names != expected_archived_names:
        errors.append(
            "deprecated skill archive differs from expected inventory: "
            f"expected={sorted(expected_archived_names)}, actual={sorted(archived_names)}"
        )
    if archived_names & actual_names:
        errors.append(
            "deprecated skills appear in active inventory: "
            f"{sorted(archived_names & actual_names)}"
        )

    configured = compatibility.get("skills", {})
    if not isinstance(configured, dict):
        return ["compatibility manifest 'skills' must be an object"]
    if set(configured) != actual_names:
        errors.append(
            "compatibility manifest skills differ from skill directories: "
            f"configured={sorted(configured)}, actual={sorted(actual_names)}"
        )

    frontmatter_names: set[str] = set()
    display_names: set[str] = set()
    for skill_dir in skill_dirs:
        name = skill_dir.name
        skill_file = skill_dir / "SKILL.md"
        try:
            frontmatter = parse_frontmatter(skill_file)
        except ValidationError as exc:
            errors.append(str(exc))
            continue

        if frontmatter.get("name") != name:
            errors.append(f"skill name does not match directory: {name}")
        if name in frontmatter_names:
            errors.append(f"duplicate skill name: {name}")
        frontmatter_names.add(name)
        invocation = frontmatter.get("disable-model-invocation")
        if invocation not in {None, "true"}:
            errors.append(f"invalid disable-model-invocation value: {name}")

        contract = configured.get(name, {})
        if not isinstance(contract, dict):
            errors.append(f"compatibility contract must be an object: {name}")
            continue
        dependencies = contract.get("requires", [])
        if not isinstance(dependencies, list) or not all(
            isinstance(dependency, str) for dependency in dependencies
        ):
            errors.append(f"compatibility requires must be a string list: {name}")
            dependencies = []
        for dependency in dependencies:
            if dependency == name:
                errors.append(f"skill cannot depend on itself: {name}")
            elif dependency not in actual_names:
                errors.append(f"unknown skill dependency for {name}: {dependency}")
        support = contract.get("support")
        sidecar = skill_dir / "agents" / "openai.yaml"
        if support == CROSS_PLATFORM:
            if not sidecar.is_file():
                errors.append(f"missing Codex sidecar: {name}")
            else:
                try:
                    display, short, implicit = parse_openai_sidecar(sidecar)
                    if display in display_names:
                        errors.append(f"duplicate Codex display name: {display}")
                    display_names.add(display)
                    if not short.strip():
                        errors.append(f"empty Codex short description: {name}")
                    explicit = frontmatter.get("disable-model-invocation") == "true"
                    if explicit and implicit is not False:
                        errors.append(f"Codex implicit invocation must be disabled: {name}")
                    if not explicit and implicit is False:
                        errors.append(f"Codex implicit invocation must be enabled: {name}")
                except ValidationError as exc:
                    errors.append(str(exc))

            for markdown in skill_dir.rglob("*.md"):
                text = markdown.read_text(encoding="utf-8")
                for forbidden in (
                    "~/.claude/skills",
                    "CLAUDE_SKILL_DIR",
                    "CLAUDE_PLUGIN_ROOT",
                    "--dangerously-skip-permissions",
                    "bypassPermissions",
                ):
                    if forbidden in text:
                        errors.append(
                            f"cross-platform skill contains Claude-only binding {forbidden!r}: "
                            f"{markdown.relative_to(ROOT)}"
                        )
        elif support == CLAUDE_ONLY:
            if sidecar.exists():
                errors.append(f"Claude-only skill must not have a Codex sidecar: {name}")
            if not contract.get("limitations"):
                errors.append(f"Claude-only skill must explain its limitations: {name}")
        else:
            errors.append(f"invalid support value for {name}: {support!r}")

        for markdown in skill_dir.rglob("*.md"):
            errors.extend(validate_markdown_links(markdown))

    agents = ROOT / "AGENTS.md"
    if not agents.is_symlink() or agents.readlink() != Path("CLAUDE.md"):
        errors.append("AGENTS.md must be a relative symlink to CLAUDE.md")

    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    for required in (
        "claude plugin marketplace add fcdtc/felix-plugins",
        "codex plugin marketplace add fcdtc/felix-plugins",
        ".codex-plugin/plugin.json",
        "archive/deprecated-skills",
        "Claude Code",
        "Codex",
    ):
        if required not in readme:
            errors.append(f"README is missing required compatibility text: {required}")

    return errors


def main() -> int:
    errors = validate()
    if errors:
        print(f"skill validation failed ({len(errors)} error(s)):", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("skill validation passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
