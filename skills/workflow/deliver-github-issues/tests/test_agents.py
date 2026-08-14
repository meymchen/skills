from __future__ import annotations

import json
from pathlib import Path

import pytest

from deliver_github_issues import agents
from deliver_github_issues import metadata as metadata_module
from deliver_github_issues.commands import CommandResult


def _state(provider: str) -> dict[str, object]:
    return {
        "agents": {"primary": provider, "metadata": "opencode"},
        "policy": {"primaryTimeoutMinutes": 60},
        "current": {
            "number": 123,
            "baseSha": "a" * 40,
            "testedSha": None,
            "checkboxes": [],
            "localChecks": [],
            "ciChecks": [],
            "issueUrl": "https://github.com/acme/widgets/issues/123",
        },
    }


def _implementation_result() -> dict[str, object]:
    return {
        "status": "completed",
        "summary": "implemented",
        "commitSha": None,
        "changedFiles": ["src/feature.py"],
        "reviewSummary": "Initial review complete.",
        "usedSkills": ["implement", "tdd", "code-review"],
        "tests": [{"command": "pytest", "exitCode": 0}],
        "blockers": [],
    }


@pytest.mark.parametrize(
    ("provider", "prefix", "skill_marker"),
    [
        ("codex", "$implement https://github.com/acme/widgets/issues/123", "$tdd"),
        ("claude", "/implement https://github.com/acme/widgets/issues/123", "/tdd"),
    ],
)
def test_implementation_starts_with_provider_specific_implement_invocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    prefix: str,
    skill_marker: str,
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: str, arguments: list[str], **kwargs: object) -> CommandResult:
        captured.update(command=command, arguments=arguments, prompt=kwargs["input_text"])
        value = _implementation_result()
        if command == "codex":
            output = Path(arguments[arguments.index("--output-last-message") + 1])
            output.write_text(json.dumps(value), encoding="utf-8")
            return CommandResult("{}", "", 0, "codex exec")
        return CommandResult(json.dumps({"structured_output": value}), "", 0, "claude -p")

    monkeypatch.setattr(agents, "run_command", fake_run)
    state = _state(provider)

    result = agents.invoke_agent_phase(
        "implement",
        state["policy"],
        state,
        {"number": 123, "skills": ["implement", "tdd"], "instruction": ""},
        {"number": 123, "title": "Ship it"},
        tmp_path,
        tmp_path,
    )

    assert result["status"] == "completed"
    prompt = str(captured["prompt"])
    assert prompt.startswith(prefix)
    assert skill_marker in prompt
    assert "Do not push, create or edit a pull request, or modify the issue" in prompt
    assert "--model" not in captured["arguments"]
    if provider == "claude":
        arguments = captured["arguments"]
        settings = json.loads(
            Path(arguments[arguments.index("--settings") + 1]).read_text(encoding="utf-8")
        )
        assert settings["sandbox"]["failIfUnavailable"] is True
        assert settings["sandbox"]["allowUnsandboxedCommands"] is False
        assert settings["sandbox"]["network"]["deniedDomains"] == ["*"]


def test_opencode_primary_uses_shared_skills_and_validates_event_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: str, arguments: list[str], **kwargs: object) -> CommandResult:
        captured.update(command=command, arguments=arguments, **kwargs)
        output = "\n".join(
            [
                json.dumps({"type": "session.status", "status": "running"}),
                json.dumps(
                    {"type": "text", "part": {"text": json.dumps(_implementation_result())}}
                ),
            ]
        )
        return CommandResult(output, "", 0, "opencode run")

    monkeypatch.setattr(agents, "run_command", fake_run)

    result = agents.invoke_agent_phase(
        "implement",
        _state("opencode")["policy"],
        _state("opencode"),
        {"number": 123, "skills": ["implement", "tdd"], "instruction": ""},
        {"number": 123, "title": "Ship it"},
        tmp_path,
        tmp_path,
    )

    assert result["status"] == "completed"
    assert captured["command"] == "opencode"
    assert captured["arguments"][:2] == ["run", "--format"]
    assert str(captured["arguments"][-1]).startswith(
        "Use the implement skill for https://github.com/acme/widgets/issues/123"
    )
    inline = json.loads(captured["env"]["OPENCODE_CONFIG_CONTENT"])
    assert inline["permission"]["bash"]["git push *"] == "deny"
    assert inline["permission"]["bash"]["gh *"] == "deny"


def test_kimi_primary_uses_shared_skills_and_validates_event_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}
    agents_home = tmp_path / ".agents"
    monkeypatch.setenv("DGI_AGENTS_HOME", str(agents_home))

    def fake_run(command: str, arguments: list[str], **kwargs: object) -> CommandResult:
        captured.update(command=command, arguments=arguments, **kwargs)
        output = json.dumps(
            {
                "type": "message",
                "role": "assistant",
                "content": json.dumps(_implementation_result()),
            }
        )
        return CommandResult(output, "", 0, "kimi -p")

    monkeypatch.setattr(agents, "run_command", fake_run)

    result = agents.invoke_agent_phase(
        "implement",
        _state("kimi")["policy"],
        _state("kimi"),
        {"number": 123, "skills": ["implement", "tdd"], "instruction": ""},
        {"number": 123, "title": "Ship it"},
        tmp_path,
        tmp_path,
    )

    assert result["status"] == "completed"
    assert captured["command"] == "kimi"
    arguments = captured["arguments"]
    assert arguments[:1] == ["-p"]
    assert "--auto" in arguments
    assert "--output-format" in arguments and "stream-json" in arguments
    assert Path(arguments[arguments.index("--skills-dir") + 1]) == agents_home / "skills"
    assert str(arguments[1]).startswith(
        "Use the implement skill for https://github.com/acme/widgets/issues/123"
    )
    assert "--model" not in arguments


def test_opencode_review_denies_general_shell_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: str, arguments: list[str], **kwargs: object) -> CommandResult:
        captured.update(arguments=arguments, **kwargs)
        value = {
            "status": "passed",
            "summary": "reviewed",
            "usedSkills": ["code-review"],
            "findings": [],
        }
        return CommandResult(
            json.dumps({"type": "text", "part": {"text": json.dumps(value)}}),
            "",
            0,
            f"{command} run",
        )

    monkeypatch.setattr(agents, "run_command", fake_run)

    agents.invoke_agent_phase(
        "review",
        _state("opencode")["policy"],
        _state("opencode"),
        {"number": 123, "skills": ["implement", "tdd"], "instruction": ""},
        {"number": 123, "title": "Ship it"},
        tmp_path,
        tmp_path,
    )

    inline = json.loads(captured["env"]["OPENCODE_CONFIG_CONTENT"])
    assert inline["permission"]["edit"] == "deny"
    assert inline["permission"]["bash"]["*"] == "deny"
    assert inline["permission"]["bash"]["git diff *"] == "allow"


def test_kimi_review_selects_the_read_only_plan_agent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: str, arguments: list[str], **kwargs: object) -> CommandResult:
        captured["arguments"] = arguments
        value = {
            "status": "passed",
            "summary": "reviewed",
            "usedSkills": ["code-review"],
            "findings": [],
        }
        return CommandResult(
            json.dumps({"type": "message", "content": json.dumps(value)}), "", 0, command
        )

    monkeypatch.setattr(agents, "run_command", fake_run)

    agents.invoke_agent_phase(
        "review",
        _state("kimi")["policy"],
        _state("kimi"),
        {"number": 123, "skills": ["implement", "tdd"], "instruction": ""},
        {"number": 123, "title": "Ship it"},
        tmp_path,
        tmp_path,
    )

    arguments = captured["arguments"]
    assert "--plan" not in arguments
    assert arguments[arguments.index("--agent") + 1] == "plan"


def test_review_invokes_only_code_review(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: str, arguments: list[str], **kwargs: object) -> CommandResult:
        captured.update(arguments=arguments, prompt=kwargs["input_text"])
        value = {
            "status": "passed",
            "summary": "reviewed",
            "usedSkills": ["code-review"],
            "findings": [],
        }
        output = Path(arguments[arguments.index("--output-last-message") + 1])
        output.write_text(json.dumps(value), encoding="utf-8")
        return CommandResult("{}", "", 0, "codex exec")

    monkeypatch.setattr(agents, "run_command", fake_run)
    state = _state("codex")

    agents.invoke_agent_phase(
        "review",
        state["policy"],
        state,
        {"number": 123, "skills": ["implement", "tdd"], "instruction": ""},
        {"number": 123, "title": "Ship it"},
        tmp_path,
        tmp_path,
    )

    prompt = str(captured["prompt"])
    assert prompt.startswith("$code-review " + "a" * 40)
    assert "Invoke only code-review" in prompt
    assert "$implement" not in prompt
    assert "$tdd" not in prompt


@pytest.mark.parametrize("primary", ("codex", "claude", "opencode", "kimi"))
@pytest.mark.parametrize("metadata", ("codex", "claude", "opencode", "kimi"))
def test_every_primary_and_metadata_route_uses_the_selected_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, primary: str, metadata: str
) -> None:
    commands: list[str] = []

    def fake_primary(command: str, arguments: list[str], **kwargs: object) -> CommandResult:
        commands.append(f"primary:{command}")
        value = _implementation_result()
        if command == "codex":
            path = Path(arguments[arguments.index("--output-last-message") + 1])
            path.write_text(json.dumps(value), encoding="utf-8")
            return CommandResult("{}", "", 0, command)
        if command == "claude":
            return CommandResult(json.dumps({"structured_output": value}), "", 0, command)
        return CommandResult(
            json.dumps({"type": "message", "content": json.dumps(value)}), "", 0, command
        )

    metadata_value = {
        "commitTitle": "Ship it (#123)",
        "prTitle": "Ship it (#123)",
        "summary": "implemented",
    }

    def fake_metadata(command: str, arguments: list[str], **kwargs: object) -> CommandResult:
        commands.append(f"metadata:{command}")
        if command == "codex":
            path = Path(arguments[arguments.index("--output-last-message") + 1])
            path.write_text(json.dumps(metadata_value), encoding="utf-8")
            return CommandResult("{}", "", 0, command)
        return CommandResult(
            json.dumps({"type": "message", "content": json.dumps(metadata_value)}), "", 0, command
        )

    monkeypatch.setattr(agents, "run_command", fake_primary)
    primary_state = _state(primary)
    agents.invoke_agent_phase(
        "implement",
        primary_state["policy"],
        primary_state,
        {"number": 123, "skills": ["implement", "tdd"], "instruction": ""},
        {"number": 123, "title": "Ship it"},
        tmp_path,
        tmp_path,
    )

    monkeypatch.setattr(metadata_module, "run_command", fake_metadata)
    metadata_state = {
        "agents": {"primary": primary, "metadata": metadata},
        "current": {
            "number": 123,
            "title": "Ship it",
            "implementation": {"summary": "implemented"},
            "localChecks": [],
        },
    }
    metadata_module.delivery_metadata({"metadataTimeoutMinutes": 5}, metadata_state, tmp_path)

    assert commands == [f"primary:{primary}", f"metadata:{metadata}"]
