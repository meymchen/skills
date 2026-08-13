from __future__ import annotations

import json
from pathlib import Path

import pytest

from deliver_github_issues import agents
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
        value = {
            "status": "completed",
            "summary": "implemented",
            "commitSha": None,
            "changedFiles": ["src/feature.py"],
            "reviewSummary": "Initial review complete.",
            "usedSkills": ["implement", "tdd", "code-review"],
            "tests": [{"command": "pytest", "exitCode": 0}],
            "blockers": [],
        }
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
