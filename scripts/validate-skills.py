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


def parse_openai_sidecar(path: Path) -> tuple[str, str, bool]:
    text = path.read_text(encoding="utf-8")
    match = re.fullmatch(
        r'interface:\n'
        r'  display_name: "([^"\n]+)"\n'
        r'  short_description: "([^"\n]+)"\n'
        r'policy:\n'
        r'  allow_implicit_invocation: (true|false)\n?',
        text,
    )
    if not match:
        raise ValidationError(
            f"invalid Codex sidecar structure: {display_path(path)}"
        )
    return match.group(1), match.group(2), match.group(3) == "true"


def validate_markdown_links(path: Path) -> list[str]:
    errors: list[str] = []
    text = path.read_text(encoding="utf-8")
    for raw_target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
        if raw_target.startswith("<") and raw_target.endswith(">"):
            raw_target = raw_target[1:-1]
        parsed = urlsplit(raw_target)
        if parsed.scheme or parsed.netloc or not parsed.path:
            continue
        decoded = unquote(parsed.path)
        if not (path.parent / decoded).exists():
            errors.append(
                f"broken local link in {display_path(path)}: {raw_target}"
            )
    return errors


def validate() -> list[str]:
    errors: list[str] = []

    try:
        marketplace = load_json(ROOT / ".claude-plugin" / "marketplace.json")
        plugin = load_json(PLUGIN_ROOT / ".claude-plugin" / "plugin.json")
        compatibility = load_json(COMPATIBILITY)
    except ValidationError as exc:
        return [str(exc)]

    entries = marketplace.get("plugins", [])
    if len(entries) != 1:
        errors.append("marketplace must contain exactly one plugin")
    else:
        entry = entries[0]
        if entry.get("name") != plugin.get("name"):
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
                errors.append("marketplace plugin source is missing plugin.json")

    skill_dirs = sorted(path.parent for path in SKILLS_ROOT.glob("*/SKILL.md"))
    actual_names = {path.name for path in skill_dirs}
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
        if frontmatter.get("disable-model-invocation") != "true":
            errors.append(f"skill must require explicit invocation: {name}")

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
        expected_dependencies = ["note"] if name in {
            "note-lint", "note-polish", "note-query"
        } else []
        if dependencies != expected_dependencies:
            errors.append(
                f"unexpected skill dependencies for {name}: "
                f"expected={expected_dependencies}, actual={dependencies}"
            )

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
                    if implicit:
                        errors.append(f"Codex implicit invocation must be disabled: {name}")
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
        "/plugin marketplace add fcdtc/felix-plugins",
        "npx skills@latest add fcdtc/felix-plugins",
        "ticket-loop",
        "Claude Code only",
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
