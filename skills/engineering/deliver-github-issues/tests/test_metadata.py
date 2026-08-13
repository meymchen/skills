from __future__ import annotations

import json
from pathlib import Path

import pytest

from deliver_github_issues import metadata
from deliver_github_issues.commands import CommandResult
from deliver_github_issues.contracts import ContractError


def _state(provider: str) -> dict[str, object]:
    return {
        "agents": {"primary": "codex", "metadata": provider},
        "current": {
            "number": 123,
            "title": "Ship metadata safely",
            "implementation": {"summary": "Added the requested behavior."},
            "localChecks": [{"command": "uv run pytest", "exitCode": 0}],
        },
    }


def _metadata_text() -> str:
    return json.dumps(
        {
            "commitTitle": "Ship metadata safely (#123)",
            "prTitle": "Ship metadata safely (#123)",
            "summary": "Added the requested behavior.",
        }
    )


def test_opencode_runs_with_effective_deny_all_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: str, arguments: list[str], **kwargs: object) -> CommandResult:
        captured.update(command=command, arguments=arguments, **kwargs)
        return CommandResult(
            json.dumps({"type": "text", "part": {"text": _metadata_text()}}),
            "",
            0,
            "opencode run",
        )

    monkeypatch.setattr(metadata, "run_command", fake_run)

    result = metadata.delivery_metadata({"metadataTimeoutMinutes": 5}, _state("opencode"), tmp_path)

    assert result["commitTitle"].endswith("(#123)")
    assert captured["command"] == "opencode"
    arguments = captured["arguments"]
    assert arguments[:2] == ["run", "--pure"]
    assert "--format" in arguments and "json" in arguments
    assert Path(captured["cwd"]) != tmp_path
    inline = json.loads(captured["env"]["OPENCODE_CONFIG_CONTENT"])
    assert inline["tools"] == {"*": False}
    assert inline["permission"] == {"*": "deny"}
    assert inline["agent"]["metadata"]["tools"] == {"*": False}


def test_kimi_uses_an_explicit_agent_with_no_tools_or_subagents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: str, arguments: list[str], **kwargs: object) -> CommandResult:
        agent_path = Path(arguments[arguments.index("--agent-file") + 1])
        captured.update(
            command=command,
            arguments=arguments,
            agent=agent_path.read_text(encoding="utf-8"),
            **kwargs,
        )
        return CommandResult(
            json.dumps(
                {
                    "type": "message",
                    "role": "assistant",
                    "content": _metadata_text(),
                }
            ),
            "",
            0,
            "kimi -p",
        )

    monkeypatch.setattr(metadata, "run_command", fake_run)

    result = metadata.delivery_metadata({"metadataTimeoutMinutes": 5}, _state("kimi"), tmp_path)

    assert result["prTitle"].endswith("(#123)")
    assert captured["command"] == "kimi"
    assert "tools: []" in captured["agent"]
    assert "subagents: []" in captured["agent"]
    assert "--skills-dir" in captured["arguments"]
    assert Path(captured["env"]["KIMI_CODE_HOME"]) != Path.home()


def test_metadata_rejects_any_tool_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(command: str, arguments: list[str], **kwargs: object) -> CommandResult:
        output = "\n".join(
            [
                json.dumps({"role": "tool", "tool_call_id": "1", "content": "ran bash"}),
                json.dumps({"type": "text", "part": {"text": _metadata_text()}}),
            ]
        )
        return CommandResult(output, "", 0, f"{command} run")

    monkeypatch.setattr(metadata, "run_command", fake_run)

    with pytest.raises(ContractError, match="tool event"):
        metadata.delivery_metadata({"metadataTimeoutMinutes": 5}, _state("opencode"), tmp_path)
