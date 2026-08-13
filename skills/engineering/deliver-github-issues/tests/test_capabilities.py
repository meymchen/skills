from __future__ import annotations

import json
from pathlib import Path

import pytest

from deliver_github_issues import capabilities
from deliver_github_issues.commands import CommandError, CommandResult


def _install_skills(home: Path) -> None:
    locked: dict[str, object] = {"version": 3, "skills": {}}
    for name in ("implement", "tdd", "code-review"):
        path = home / "skills" / name
        path.mkdir(parents=True)
        (path / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
        locked["skills"][name] = {
            "source": "mattpocock/skills",
            "skillFolderHash": f"hash-{name}",
        }
    (home / ".skill-lock.json").write_text(json.dumps(locked), encoding="utf-8")


def test_capability_validation_records_versions_and_effective_deny_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agents_home = tmp_path / ".agents"
    _install_skills(agents_home)
    monkeypatch.setenv("DGI_AGENTS_HOME", str(agents_home))
    monkeypatch.setattr(capabilities.shutil, "which", lambda command: command)

    def fake_run(command: str, arguments: list[str], **kwargs: object) -> CommandResult:
        if arguments == ["--version"]:
            output = "codex-cli 0.147.0" if command == "codex" else "1.18.18"
        elif arguments[:2] == ["debug", "config"] or arguments[:2] == ["debug", "agent"]:
            output = json.dumps({"tools": {"*": False}, "permission": {"*": "deny"}})
        elif arguments == ["auth", "list"]:
            output = "openai"
        else:
            output = "authenticated"
        return CommandResult(output, "", 0, f"{command} {' '.join(arguments)}")

    monkeypatch.setattr(capabilities, "run_command", fake_run)

    report = capabilities.validate_capabilities(
        {"primary": "codex", "metadata": "opencode"}, tmp_path, tmp_path / "probe.log"
    )

    assert report == {
        "skill:implement": "hash-implement",
        "skill:tdd": "hash-tdd",
        "skill:code-review": "hash-code-review",
        "codex": "codex-cli 0.147.0",
        "opencode": "1.18.18",
    }


def test_capability_validation_rejects_old_metadata_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agents_home = tmp_path / ".agents"
    _install_skills(agents_home)
    monkeypatch.setenv("DGI_AGENTS_HOME", str(agents_home))
    monkeypatch.setattr(capabilities.shutil, "which", lambda command: command)
    monkeypatch.setattr(
        capabilities,
        "run_command",
        lambda command, arguments, **kwargs: CommandResult(
            "codex-cli 0.147.0" if command == "codex" else "1.17.0",
            "",
            0,
            command,
        ),
    )

    with pytest.raises(CommandError, match="require >= 1.18.18"):
        capabilities.validate_capabilities(
            {"primary": "codex", "metadata": "opencode"},
            tmp_path,
            tmp_path / "probe.log",
        )


def test_kimi_capability_requires_custom_agent_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agents_home = tmp_path / ".agents"
    _install_skills(agents_home)
    monkeypatch.setenv("DGI_AGENTS_HOME", str(agents_home))
    monkeypatch.setattr(capabilities.shutil, "which", lambda command: command)

    def fake_run(command: str, arguments: list[str], **kwargs: object) -> CommandResult:
        if arguments == ["--version"]:
            output = "codex-cli 0.147.0" if command == "codex" else "0.35.0"
        elif arguments == ["--help"]:
            output = "--agent-file --skills-dir --output-format"
        elif arguments == ["doctor"]:
            output = "ok"
        else:
            output = "authenticated"
        return CommandResult(output, "", 0, command)

    monkeypatch.setattr(capabilities, "run_command", fake_run)

    report = capabilities.validate_capabilities(
        {"primary": "codex", "metadata": "kimi"}, tmp_path, tmp_path / "probe.log"
    )

    assert report["kimi"] == "0.35.0"


def test_formal_capability_validation_runs_primary_and_metadata_probes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agents_home = tmp_path / ".agents"
    _install_skills(agents_home)
    monkeypatch.setenv("DGI_AGENTS_HOME", str(agents_home))
    monkeypatch.setattr(capabilities.shutil, "which", lambda command: command)
    primary_probed = False

    def fake_run(command: str, arguments: list[str], **kwargs: object) -> CommandResult:
        nonlocal primary_probed
        if arguments == ["--version"]:
            output = "codex-cli 0.147.0" if command == "codex" else "1.18.18"
        elif arguments[:2] in (["debug", "config"], ["debug", "agent"]):
            output = json.dumps({"tools": {"*": False}, "permission": {"*": "deny"}})
        elif arguments == ["auth", "list"]:
            output = "openai"
        else:
            if any("CAPABILITY_PROBE" in argument for argument in arguments):
                primary_probed = True
                result_path = Path(arguments[arguments.index("--output-last-message") + 1])
                result_path.write_text(
                    "typechecking; full test suite; code-review; current branch",
                    encoding="utf-8",
                )
            output = "authenticated"
        return CommandResult(output, "", 0, command)

    monkeypatch.setattr(capabilities, "run_command", fake_run)
    monkeypatch.setattr(
        capabilities,
        "_run_opencode",
        lambda *args: json.dumps({"type": "text", "part": {"text": "METADATA_CAPABILITY_OK"}}),
    )

    capabilities.validate_capabilities(
        {"primary": "codex", "metadata": "opencode"},
        tmp_path,
        tmp_path / "probe.log",
        dynamic=True,
    )

    assert primary_probed
