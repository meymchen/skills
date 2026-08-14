from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path

from deliver_github_issues.agents import _opencode_primary_environment
from deliver_github_issues.commands import CommandError, command_json, run_command
from deliver_github_issues.metadata import (
    _claude_metadata_arguments,
    _codex_metadata_arguments,
    _has_tool_event,
    _opencode_environment,
    _run_kimi,
    _run_opencode,
    _text_candidates,
)

_REQUIRED_SKILLS = ("implement", "tdd", "code-review")
_VERSION = re.compile(r"(?<!\d)(\d+)\.(\d+)\.(\d+)(?!\d)")
_MINIMUM_METADATA_VERSIONS = {"opencode": (1, 18, 18), "kimi": (0, 29, 0)}


def _version(provider: str, log_path: Path) -> tuple[str, tuple[int, int, int]]:
    result = run_command(provider, ["--version"], log_path=log_path)
    text = (result.output + " " + result.stderr).strip()
    match = _VERSION.search(text)
    if not match:
        raise CommandError(f"Could not parse {provider} version from: {text or '<empty>'}")
    return text, tuple(int(part) for part in match.groups())


def _validate_skill_source(primary: str) -> dict[str, str]:
    agents_home = Path(os.environ.get("DGI_AGENTS_HOME", Path.home() / ".agents"))
    lock_path = agents_home / ".skill-lock.json"
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CommandError(f"Cannot read npx skills lock file: {lock_path}") from error
    canonical: dict[str, Path] = {}
    revisions: dict[str, str] = {}
    for skill in _REQUIRED_SKILLS:
        skill_file = agents_home / "skills" / skill / "SKILL.md"
        if not skill_file.is_file():
            raise CommandError(
                f"Required mattpocock/skills skill is unavailable: {skill_file}. "
                "Install it with npx skills."
            )
        if f"name: {skill}" not in skill_file.read_text(encoding="utf-8"):
            raise CommandError(f"Skill metadata does not declare name: {skill}: {skill_file}")
        locked = lock.get("skills", {}).get(skill, {})
        if locked.get("source") != "mattpocock/skills" or not locked.get("skillFolderHash"):
            raise CommandError(f"Skill {skill} is not locked to mattpocock/skills in {lock_path}.")
        canonical[skill] = skill_file.resolve()
        revisions[f"skill:{skill}"] = locked["skillFolderHash"]
    if primary != "claude":
        return revisions
    claude_home = Path(os.environ.get("DGI_CLAUDE_HOME", Path.home() / ".claude"))
    for skill, source in canonical.items():
        linked = claude_home / "skills" / skill / "SKILL.md"
        if not linked.is_file() or linked.resolve() != source:
            raise CommandError(
                f"Claude skill {skill} must link to the npx skills source {source}: {linked}"
            )
    return revisions


def _validate_opencode_deny_all(log_path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="deliver-opencode-probe-") as temporary_name:
        temporary = Path(temporary_name)
        environment = _opencode_environment(temporary / "xdg-config")
        config = command_json(
            run_command(
                "opencode",
                ["debug", "config", "--pure"],
                cwd=temporary,
                env=environment,
                log_path=log_path,
            ),
            "opencode effective config",
        )
        agent = command_json(
            run_command(
                "opencode",
                ["debug", "agent", "metadata", "--pure"],
                cwd=temporary,
                env=environment,
                log_path=log_path,
            ),
            "opencode metadata agent",
        )
    if (
        config.get("tools", {}).get("*") is not False
        or config.get("permission", {}).get("*") != "deny"
    ):
        raise CommandError("OpenCode effective global configuration is not deny-all.")
    tools = agent.get("tools", {})
    permissions = agent.get("permission", {})
    if isinstance(permissions, list):
        wildcard = next(
            (
                rule
                for rule in reversed(permissions)
                if rule.get("permission") == "*" and rule.get("pattern") == "*"
            ),
            {},
        )
        deny_all = wildcard.get("action") == "deny"
    else:
        deny_all = permissions.get("*") == "deny"
    if not tools or any(enabled is not False for enabled in tools.values()) or not deny_all:
        raise CommandError("OpenCode effective metadata agent is not deny-all.")


def _validate_kimi_agent_support(log_path: Path) -> None:
    help_text = run_command("kimi", ["--help"], log_path=log_path).output
    for option in ("--agent-file", "--skills-dir", "--output-format"):
        if option not in help_text:
            raise CommandError(f"Kimi Code CLI does not support required option {option}.")


def _validate_help_options(
    provider: str, arguments: list[str], options: tuple[str, ...], log_path: Path
) -> None:
    help_text = run_command(provider, arguments, log_path=log_path).output
    for option in options:
        if option not in help_text:
            raise CommandError(f"{provider} does not support required option {option}.")


def _validate_metadata_support(provider: str, log_path: Path) -> None:
    if provider == "codex":
        _validate_help_options(
            provider,
            ["exec", "--help"],
            ("--ignore-user-config", "--ignore-rules", "--output-schema", "--json"),
            log_path,
        )
    elif provider == "claude":
        _validate_help_options(
            provider,
            ["--help"],
            ("--safe-mode", "--tools", "--json-schema", "--output-format"),
            log_path,
        )
    elif provider == "opencode":
        _validate_opencode_deny_all(log_path)
    elif provider == "kimi":
        _validate_kimi_agent_support(log_path)
    else:
        raise CommandError(f"Unsupported metadata provider: {provider}")


def _validate_auth(provider: str, log_path: Path) -> None:
    if provider == "codex":
        run_command("codex", ["login", "status"], log_path=log_path)
    elif provider == "claude":
        run_command("claude", ["auth", "status"], log_path=log_path)
    elif provider == "opencode":
        result = run_command("opencode", ["auth", "list"], log_path=log_path)
        if not result.output.strip():
            raise CommandError("OpenCode has no configured authentication provider.")
    elif provider == "kimi":
        run_command("kimi", ["doctor"], log_path=log_path)
    else:
        raise CommandError(f"Unsupported provider: {provider}")


def _dynamic_primary_probe(provider: str, root: Path, log_path: Path) -> None:
    request = (
        "CAPABILITY_PROBE: invoke this skill without doing ticket work. Summarize its required "
        "test cadence, final review step, and commit destination. Make no changes."
    )
    with tempfile.TemporaryDirectory(prefix="deliver-primary-probe-") as temporary_name:
        result_path = Path(temporary_name) / "result.txt"
        if provider == "codex":
            arguments = [
                "exec",
                "--ephemeral",
                "--sandbox",
                "read-only",
                "--output-last-message",
                str(result_path),
                "-C",
                str(root),
                f"$implement {request}",
            ]
        elif provider == "claude":
            settings_path = Path(temporary_name) / "claude-sandbox-settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "sandbox": {
                            "enabled": True,
                            "failIfUnavailable": True,
                            "autoAllowBashIfSandboxed": True,
                            "allowUnsandboxedCommands": False,
                            "excludedCommands": [],
                            "network": {"allowedDomains": [], "deniedDomains": ["*"]},
                        }
                    }
                ),
                encoding="utf-8",
            )
            arguments = [
                "--print",
                "--no-session-persistence",
                "--settings",
                str(settings_path),
                "--permission-mode",
                "plan",
                "--allowedTools",
                "Read",
                f"/implement {request}",
            ]
        elif provider == "opencode":
            arguments = [
                "run",
                "--format",
                "json",
                "--dir",
                str(root),
                f"Use the implement skill. {request}",
            ]
        elif provider == "kimi":
            skills_dir = Path(os.environ.get("DGI_AGENTS_HOME", Path.home() / ".agents")) / "skills"
            arguments = [
                "-p",
                f"Use the implement skill. {request}",
                "--agent",
                "plan",
                "--skills-dir",
                str(skills_dir),
                "--output-format",
                "stream-json",
            ]
        else:
            raise CommandError(f"Unsupported primary provider: {provider}")
        result = run_command(
            provider,
            arguments,
            cwd=root,
            env=_opencode_primary_environment("review") if provider == "opencode" else None,
            log_path=log_path,
            timeout_seconds=120,
        )
        output = (
            result_path.read_text(encoding="utf-8")
            if provider == "codex" and result_path.is_file()
            else result.output
        ).lower()
    expected = ("typecheck", "full test suite", "code-review", "current branch")
    if not all(term in output for term in expected):
        raise CommandError(f"{provider} did not demonstrate that it resolved the implement skill.")


def _dynamic_metadata_probe(provider: str, log_path: Path) -> None:
    texts: list[str] = []
    with tempfile.TemporaryDirectory(prefix="deliver-metadata-probe-") as temporary_name:
        temporary = Path(temporary_name)
        prompt = "Return exactly METADATA_CAPABILITY_OK without using tools."
        if provider == "codex":
            result_path = temporary / "result.txt"
            result = run_command(
                "codex",
                [
                    *_codex_metadata_arguments(temporary),
                    "--output-last-message",
                    str(result_path),
                    "-",
                ],
                cwd=temporary,
                input_text=prompt,
                log_path=log_path,
                timeout_seconds=120,
            )
            output = result.output
            if result_path.is_file():
                texts.append(result_path.read_text(encoding="utf-8"))
        elif provider == "claude":
            output = run_command(
                "claude",
                _claude_metadata_arguments(),
                cwd=temporary,
                input_text=prompt,
                log_path=log_path,
                timeout_seconds=120,
            ).output
        elif provider == "opencode":
            output = _run_opencode(prompt, temporary, 120, log_path)
        elif provider == "kimi":
            output = _run_kimi(prompt, temporary, 120, log_path)
        else:
            raise CommandError(f"Unsupported metadata provider: {provider}")
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise CommandError(f"{provider} capability probe returned invalid JSON.") from error
        if not isinstance(event, dict) or _has_tool_event(event):
            raise CommandError(f"{provider} capability probe exposed a tool event.")
        texts.extend(_text_candidates(event))
    if not any("METADATA_CAPABILITY_OK" in text for text in texts):
        raise CommandError(f"{provider} capability probe produced no expected assistant text.")


def validate_capabilities(
    agents: dict[str, str], root: Path, log_path: Path, *, dynamic: bool = False
) -> dict[str, str]:
    for command in (agents["primary"], agents["metadata"]):
        if shutil.which(command) is None:
            raise CommandError(f"Required command is unavailable: {command}")
    versions = _validate_skill_source(agents["primary"])
    for provider in dict.fromkeys((agents["primary"], agents["metadata"])):
        text, parsed = _version(provider, log_path)
        minimum = _MINIMUM_METADATA_VERSIONS.get(provider)
        if minimum and parsed < minimum:
            required = ".".join(str(part) for part in minimum)
            raise CommandError(f"{provider} {text} is too old; require >= {required}.")
        versions[provider] = text
    for provider in dict.fromkeys((agents["primary"], agents["metadata"])):
        _validate_auth(provider, log_path)
    _validate_metadata_support(agents["metadata"], log_path)
    if dynamic:
        _dynamic_primary_probe(agents["primary"], root, log_path)
        _dynamic_metadata_probe(agents["metadata"], log_path)
    return versions
