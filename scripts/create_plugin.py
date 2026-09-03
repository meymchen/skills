# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///

"""Create, publish, synchronize, and validate dual-host plugins."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

MARKETPLACE_NAME = "meymchen-skills"
AUTHOR_NAME = "Yuming Chen"
AUTHOR_URL = "https://github.com/meymchen"
CATEGORY = "Productivity"
TEMPLATE_NAME = "_template"
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[A-Za-z-][0-9A-Za-z-]*))*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
TOKEN_RE = re.compile(r"\{\{[^{}]+}}|__plugin_name__")


class PluginError(Exception):
    """Raised when a plugin operation cannot preserve repository invariants."""


def repository_root() -> Path:
    return Path(__file__).resolve().parent.parent


def normalize_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not normalized:
        raise PluginError("plugin name is empty after normalization")
    if len(normalized) > 64:
        raise PluginError("plugin name must be at most 64 characters")
    if normalized == "template" or value.strip().lower() == TEMPLATE_NAME:
        raise PluginError("plugin name is reserved for the repository template")
    if NAME_RE.fullmatch(normalized) is None:
        raise PluginError("plugin name must normalize to lowercase kebab-case")
    return normalized


def validate_description(value: str) -> str:
    description = value.strip()
    if not description:
        raise PluginError("description must not be empty")
    if len(description) > 120:
        raise PluginError("description must be at most 120 characters")
    if len(description.splitlines()) != 1:
        raise PluginError("description must be a single line")
    if TOKEN_RE.search(description):
        raise PluginError("description must not contain template markers")
    return description


def validate_display_name(value: str) -> str:
    display_name = value.strip()
    if not display_name:
        raise PluginError("display name must not be empty")
    if len(display_name.splitlines()) != 1:
        raise PluginError("display name must be a single line")
    if TOKEN_RE.search(display_name):
        raise PluginError("display name must not contain template markers")
    return display_name


def validate_version(value: str) -> str:
    if SEMVER_RE.fullmatch(value) is None:
        raise PluginError("version must use strict semantic versioning")
    return value


def display_name_from_name(name: str) -> str:
    return " ".join(part.capitalize() for part in name.split("-"))


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise PluginError(f"missing required file: {path}") from error
    except json.JSONDecodeError as error:
        raise PluginError(f"invalid JSON in {path}: {error}") from error
    if not isinstance(payload, dict):
        raise PluginError(f"JSON root must be an object: {path}")
    return payload


def _catalog_paths(root: Path) -> tuple[Path, Path]:
    return (
        root / ".claude-plugin" / "marketplace.json",
        root / ".agents" / "plugins" / "marketplace.json",
    )


def _plugin_path(root: Path, name: str) -> Path:
    plugins_root = (root / "plugins").resolve()
    target = (plugins_root / name).resolve()
    if target.parent != plugins_root:
        raise PluginError("plugin path escapes the plugins directory")
    return target


def _render_text(path: Path, text: str, values: dict[str, str]) -> str:
    rendered = text
    for key, value in values.items():
        replacement = value
        if path.suffix == ".json":
            replacement = json.dumps(value, ensure_ascii=False)[1:-1]
        elif path.name == "SKILL.md" and key == "description":
            replacement = value.replace("'", "''")
        rendered = rendered.replace("{{" + key + "}}", replacement)
    match = TOKEN_RE.search(rendered)
    if match:
        raise PluginError(f"unresolved template marker {match.group(0)!r} in {path}")
    return rendered


def render_template(
    root: Path,
    target: Path,
    *,
    name: str,
    display_name: str,
    description: str,
    version: str,
) -> None:
    template = root / "plugins" / TEMPLATE_NAME
    if not template.is_dir():
        raise PluginError(f"missing plugin template: {template}")
    values = {
        "plugin_name": name,
        "display_name": display_name,
        "description": description,
        "version": version,
    }
    for source in sorted(template.rglob("*")):
        relative_parts = [
            part.replace("__plugin_name__", name) for part in source.relative_to(template).parts
        ]
        destination = target.joinpath(*relative_parts)
        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        text = source.read_text(encoding="utf-8")
        destination.write_text(_render_text(source, text, values), encoding="utf-8", newline="\n")


def create_plugin(
    root: Path,
    raw_name: str,
    *,
    description: str,
    display_name: str | None = None,
    version: str = "0.1.0",
) -> str:
    name = normalize_name(raw_name)
    description = validate_description(description)
    display_name = validate_display_name(display_name or display_name_from_name(name))
    version = validate_version(version)
    target = _plugin_path(root, name)
    if target.exists():
        raise PluginError(f"plugin already exists: {target}")
    try:
        render_template(
            root,
            target,
            name=name,
            display_name=display_name,
            description=description,
            version=version,
        )
        validate_plugin(root, name)
    except Exception:
        if target.exists():
            import shutil

            shutil.rmtree(target)
        raise
    return name


def _require_string(payload: dict[str, Any], key: str, location: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise PluginError(f"{location}.{key} must be a non-empty string")
    return value


def _validate_author(payload: dict[str, Any], location: str) -> None:
    author = payload.get("author")
    if not isinstance(author, dict):
        raise PluginError(f"{location}.author must be an object")
    if author.get("name") != AUTHOR_NAME or author.get("url") != AUTHOR_URL:
        raise PluginError(f"{location}.author must identify {AUTHOR_NAME} at {AUTHOR_URL}")
    if "email" in author:
        raise PluginError(f"{location}.author must not publish an email address")


def _validate_skill(plugin: Path, name: str) -> None:
    skill = plugin / "skills" / name / "SKILL.md"
    try:
        text = skill.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise PluginError(f"missing generated skill: {skill}") from error
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        raise PluginError(f"skill frontmatter is malformed: {skill}")
    frontmatter = text.split("\n---\n", 1)[0][4:]
    if f"name: {name}" not in frontmatter.splitlines():
        raise PluginError(f"skill name must match plugin name: {skill}")
    if not any(line.startswith("description: ") for line in frontmatter.splitlines()):
        raise PluginError(f"skill description is missing: {skill}")


def validate_plugin(root: Path, raw_name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    name = normalize_name(raw_name)
    plugin = _plugin_path(root, name)
    if not plugin.is_dir():
        raise PluginError(f"plugin does not exist: {plugin}")
    claude_path = plugin / ".claude-plugin" / "plugin.json"
    codex_path = plugin / ".codex-plugin" / "plugin.json"
    claude = _load_json(claude_path)
    codex = _load_json(codex_path)
    for key in ("name", "version", "description"):
        claude_value = _require_string(claude, key, str(claude_path))
        codex_value = _require_string(codex, key, str(codex_path))
        if claude_value != codex_value:
            raise PluginError(f"plugin manifests disagree on {key}")
    if claude["name"] != name:
        raise PluginError("plugin folder and manifest names must match")
    validate_version(claude["version"])
    validate_description(claude["description"])
    _validate_author(claude, str(claude_path))
    _validate_author(codex, str(codex_path))
    if claude.get("license") != "MIT" or codex.get("license") != "MIT":
        raise PluginError("both plugin manifests must declare the MIT license")
    if codex.get("skills") != "./skills/":
        raise PluginError("Codex manifest must discover skills at ./skills/")
    interface = codex.get("interface")
    if not isinstance(interface, dict):
        raise PluginError("Codex manifest interface must be an object")
    for key in (
        "displayName",
        "shortDescription",
        "longDescription",
        "developerName",
        "category",
        "defaultPrompt",
    ):
        _require_string(interface, key, f"{codex_path}.interface")
    if interface["developerName"] != AUTHOR_NAME:
        raise PluginError(f"Codex developerName must be {AUTHOR_NAME}")
    if not isinstance(interface.get("capabilities"), list):
        raise PluginError("Codex capabilities must be an array")
    _validate_skill(plugin, name)
    text_suffixes = {".json", ".md", ".toml", ".txt", ".yaml", ".yml"}
    for path in plugin.rglob("*"):
        if path.is_file() and path.suffix.lower() in text_suffixes:
            match = TOKEN_RE.search(path.read_text(encoding="utf-8"))
            if match:
                raise PluginError(f"unresolved template marker in {path}")
    return claude, codex


def _catalog_entries(payload: dict[str, Any], location: Path) -> dict[str, dict[str, Any]]:
    plugins = payload.get("plugins")
    if not isinstance(plugins, list):
        raise PluginError(f"{location}.plugins must be an array")
    entries: dict[str, dict[str, Any]] = {}
    for index, entry in enumerate(plugins):
        if not isinstance(entry, dict):
            raise PluginError(f"{location}.plugins[{index}] must be an object")
        name = _require_string(entry, "name", f"{location}.plugins[{index}]")
        if name in entries:
            raise PluginError(f"duplicate plugin {name!r} in {location}")
        if normalize_name(name) != name:
            raise PluginError(f"non-canonical plugin name {name!r} in {location}")
        entries[name] = entry
    return entries


def validate_catalogs(
    root: Path,
    claude: dict[str, Any] | None = None,
    codex: dict[str, Any] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    claude_path, codex_path = _catalog_paths(root)
    claude = claude or _load_json(claude_path)
    codex = codex or _load_json(codex_path)
    if claude.get("name") != MARKETPLACE_NAME or codex.get("name") != MARKETPLACE_NAME:
        raise PluginError(f"both marketplace names must be {MARKETPLACE_NAME}")
    if claude.get("owner") != {"name": AUTHOR_NAME}:
        raise PluginError("Claude marketplace owner metadata is invalid")
    if claude.get("description") != "Dual-host plugins maintained by Yuming Chen.":
        raise PluginError("Claude marketplace description is invalid")
    if codex.get("interface") != {"displayName": "Meymchen Skills"}:
        raise PluginError("Codex marketplace interface metadata is invalid")
    claude_entries = _catalog_entries(claude, claude_path)
    codex_entries = _catalog_entries(codex, codex_path)
    if claude_entries.keys() != codex_entries.keys():
        raise PluginError("Claude and Codex marketplaces must list the same plugins")
    for name in claude_entries:
        expected_path = f"./plugins/{name}"
        claude_entry = claude_entries[name]
        codex_entry = codex_entries[name]
        if claude_entry.get("source") != expected_path:
            raise PluginError(f"Claude source for {name} must be {expected_path}")
        if codex_entry.get("source") != {"source": "local", "path": expected_path}:
            raise PluginError(f"Codex source for {name} must be local path {expected_path}")
        if codex_entry.get("policy") != {
            "installation": "AVAILABLE",
            "authentication": "ON_INSTALL",
        }:
            raise PluginError(f"Codex policy for {name} is invalid")
        if codex_entry.get("category") != CATEGORY:
            raise PluginError(f"Codex category for {name} must be {CATEGORY}")
        plugin_claude, _ = validate_plugin(root, name)
        if claude_entry.get("version") != plugin_claude["version"]:
            raise PluginError(f"Claude marketplace version for {name} is out of sync")
        if claude_entry.get("description") != plugin_claude["description"]:
            raise PluginError(f"Claude marketplace description for {name} is out of sync")
        if claude_entry.get("author") != {"name": AUTHOR_NAME}:
            raise PluginError(f"Claude marketplace author for {name} is invalid")
    return claude_entries, codex_entries


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, indent=2, ensure_ascii=False) + "\n").encode()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_catalogs(root: Path, claude: dict[str, Any], codex: dict[str, Any]) -> None:
    validate_catalogs(root, claude, codex)
    claude_path, codex_path = _catalog_paths(root)
    originals = {
        claude_path: claude_path.read_bytes(),
        codex_path: codex_path.read_bytes(),
    }
    replacements = {
        claude_path: _json_bytes(claude),
        codex_path: _json_bytes(codex),
    }
    try:
        for path, data in replacements.items():
            _atomic_write(path, data)
    except Exception as error:
        restoration_errors: list[str] = []
        for path, data in originals.items():
            try:
                _atomic_write(path, data)
            except Exception as restore_error:  # pragma: no cover - catastrophic I/O failure
                restoration_errors.append(f"{path}: {restore_error}")
        if restoration_errors:
            details = "; ".join(restoration_errors)
            raise PluginError(
                f"catalog update failed and rollback was incomplete: {details}"
            ) from error
        raise PluginError(f"catalog update failed; original files restored: {error}") from error


def _manifest_values(root: Path, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    return validate_plugin(root, name)


def publish_plugin(root: Path, raw_name: str) -> str:
    name = normalize_name(raw_name)
    plugin_claude, plugin_codex = _manifest_values(root, name)
    claude_path, codex_path = _catalog_paths(root)
    claude = _load_json(claude_path)
    codex = _load_json(codex_path)
    claude_entries = _catalog_entries(claude, claude_path)
    codex_entries = _catalog_entries(codex, codex_path)
    if name in claude_entries or name in codex_entries:
        raise PluginError(f"plugin is already present in a marketplace: {name}")
    claude["plugins"].append(
        {
            "name": name,
            "source": f"./plugins/{name}",
            "description": plugin_claude["description"],
            "version": plugin_claude["version"],
            "author": {"name": AUTHOR_NAME},
        }
    )
    codex["plugins"].append(
        {
            "name": name,
            "source": {"source": "local", "path": f"./plugins/{name}"},
            "policy": {
                "installation": "AVAILABLE",
                "authentication": "ON_INSTALL",
            },
            "category": plugin_codex["interface"]["category"],
        }
    )
    _write_catalogs(root, claude, codex)
    return name


def sync_plugin(root: Path, raw_name: str) -> str:
    name = normalize_name(raw_name)
    plugin_claude, plugin_codex = _manifest_values(root, name)
    claude_path, codex_path = _catalog_paths(root)
    claude = _load_json(claude_path)
    codex = _load_json(codex_path)
    claude_entries = _catalog_entries(claude, claude_path)
    codex_entries = _catalog_entries(codex, codex_path)
    if name not in claude_entries or name not in codex_entries:
        raise PluginError(f"plugin must already exist in both marketplaces: {name}")
    claude_entries[name].update(
        {
            "description": plugin_claude["description"],
            "version": plugin_claude["version"],
            "author": {"name": AUTHOR_NAME},
        }
    )
    codex_entries[name]["category"] = plugin_codex["interface"]["category"]
    _write_catalogs(root, claude, codex)
    return name


def check_template(root: Path) -> None:
    with tempfile.TemporaryDirectory() as temporary_directory:
        temporary = Path(temporary_directory)
        plugin_root = temporary / "template-check"
        render_template(
            root,
            plugin_root,
            name="template-check",
            display_name="Template Check",
            description="Validate the dual-host plugin template.",
            version="0.1.0",
        )
        synthetic_root = temporary / "repository"
        (synthetic_root / "plugins").mkdir(parents=True)
        plugin_root.replace(synthetic_root / "plugins" / "template-check")
        validate_plugin(synthetic_root, "template-check")


def check_all(root: Path) -> None:
    check_template(root)
    validate_catalogs(root)
    plugins_root = root / "plugins"
    if not plugins_root.is_dir():
        raise PluginError(f"missing plugins directory: {plugins_root}")
    for plugin in sorted(plugins_root.iterdir()):
        if plugin.is_dir() and plugin.name != TEMPLATE_NAME:
            validate_plugin(root, plugin.name)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="create a draft plugin")
    create.add_argument("name")
    create.add_argument("--description", required=True)
    create.add_argument("--display-name")
    create.add_argument("--version", default="0.1.0")
    create.add_argument("--publish", action="store_true")

    publish = subparsers.add_parser("publish", help="publish a draft plugin")
    publish.add_argument("name")

    sync = subparsers.add_parser("sync", help="synchronize a published plugin")
    sync.add_argument("name")

    check = subparsers.add_parser("check", help="validate plugins and marketplaces")
    check.add_argument("name", nargs="?")
    check.add_argument("--all", action="store_true")
    return parser


def main(argv: list[str] | None = None, *, root: Path | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    root = (root or repository_root()).resolve()
    try:
        if args.command == "create":
            name = create_plugin(
                root,
                args.name,
                description=args.description,
                display_name=args.display_name,
                version=args.version,
            )
            if args.publish:
                publish_plugin(root, name)
                print(f"Created and published {name}")
            else:
                print(f"Created draft plugin {name}")
        elif args.command == "publish":
            print(f"Published {publish_plugin(root, args.name)}")
        elif args.command == "sync":
            print(f"Synchronized {sync_plugin(root, args.name)}")
        elif args.command == "check":
            if args.all and args.name:
                raise PluginError("pass a plugin name or --all, not both")
            if args.all:
                check_all(root)
                print("All plugins and marketplaces are valid")
            elif args.name:
                name = normalize_name(args.name)
                validate_plugin(root, name)
                claude_entries, codex_entries = validate_catalogs(root)
                if (name in claude_entries) != (name in codex_entries):
                    raise PluginError(f"plugin has a one-sided marketplace entry: {name}")
                print(f"Plugin {name} is valid")
            else:
                raise PluginError("check requires a plugin name or --all")
    except PluginError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
