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


def test_codex_uses_read_only_isolation_and_validates_final_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: str, arguments: list[str], **kwargs: object) -> CommandResult:
        captured.update(command=command, arguments=arguments, **kwargs)
        result_path = Path(arguments[arguments.index("--output-last-message") + 1])
        result_path.write_text(_metadata_text(), encoding="utf-8")
        return CommandResult(json.dumps({"type": "message", "role": "assistant"}), "", 0, "codex")

    monkeypatch.setattr(metadata, "run_command", fake_run)

    result = metadata.delivery_metadata({"metadataTimeoutMinutes": 5}, _state("codex"), tmp_path)

    assert result["commitTitle"].endswith("(#123)")
    assert captured["command"] == "codex"
    arguments = captured["arguments"]
    assert "--sandbox" in arguments and "read-only" in arguments
    assert "--ignore-user-config" in arguments
    assert "--ignore-rules" in arguments
    assert "--skip-git-repo-check" in arguments
    assert "mcp_servers={}" in arguments
    assert Path(captured["cwd"]) != tmp_path


def test_claude_uses_safe_mode_with_no_tools_and_validates_event_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: str, arguments: list[str], **kwargs: object) -> CommandResult:
        captured.update(command=command, arguments=arguments, **kwargs)
        output = json.dumps({"type": "message", "role": "assistant", "content": _metadata_text()})
        return CommandResult(output, "", 0, "claude --print")

    monkeypatch.setattr(metadata, "run_command", fake_run)

    result = metadata.delivery_metadata({"metadataTimeoutMinutes": 5}, _state("claude"), tmp_path)

    assert result["prTitle"].endswith("(#123)")
    assert captured["command"] == "claude"
    arguments = captured["arguments"]
    assert "--safe-mode" in arguments
    assert arguments[arguments.index("--tools") + 1] == ""
    assert arguments[arguments.index("--output-format") + 1] == "stream-json"
    assert "--verbose" in arguments
    assert "--json-schema" in arguments
    assert "--model" not in arguments
    assert Path(captured["cwd"]) != tmp_path


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
    assert Path(captured["env"]["XDG_CONFIG_HOME"]).is_relative_to(Path(captured["cwd"]))


def test_opencode_metadata_uses_the_model_opencode_resolves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: str, arguments: list[str], **kwargs: object) -> CommandResult:
        if arguments[:2] == ["debug", "config"]:
            return CommandResult(
                json.dumps({"model": "anthropic/claude-sonnet-4-6", "tools": {"*": False}}),
                "",
                0,
                "opencode debug config",
            )
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
    inline = json.loads(captured["env"]["OPENCODE_CONFIG_CONTENT"])
    assert inline["model"] == "anthropic/claude-sonnet-4-6"


def test_opencode_metadata_falls_back_to_a_pinned_model_when_none_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: str, arguments: list[str], **kwargs: object) -> CommandResult:
        if arguments[:2] == ["debug", "config"]:
            return CommandResult("{}", "", 0, "opencode debug config")
        captured.update(command=command, arguments=arguments, **kwargs)
        return CommandResult(
            json.dumps({"type": "text", "part": {"text": _metadata_text()}}),
            "",
            0,
            "opencode run",
        )

    monkeypatch.setattr(metadata, "run_command", fake_run)

    metadata.delivery_metadata({"metadataTimeoutMinutes": 5}, _state("opencode"), tmp_path)

    inline = json.loads(captured["env"]["OPENCODE_CONFIG_CONTENT"])
    assert inline["model"] == "deepseek/deepseek-v4-flash"


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


def test_kimi_isolated_home_copies_credentials_from_the_current_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source_home = tmp_path / ".kimi-code"
    source = source_home / "credentials"
    source.mkdir(parents=True)
    (source / "token.json").write_text("{}", encoding="utf-8")
    config = 'default_model = "test"\n'
    (source_home / "config.toml").write_text(config, encoding="utf-8")
    monkeypatch.delenv("KIMI_CODE_HOME", raising=False)
    monkeypatch.setattr(metadata.Path, "home", classmethod(lambda cls: tmp_path))

    def fake_run(command: str, arguments: list[str], **kwargs: object) -> CommandResult:
        isolated = Path(kwargs["env"]["KIMI_CODE_HOME"])
        assert (isolated / "credentials" / "token.json").is_file()
        assert (isolated / "config.toml").read_text(encoding="utf-8") == config
        return CommandResult(
            json.dumps({"type": "message", "role": "assistant", "content": _metadata_text()}),
            "",
            0,
            command,
        )

    monkeypatch.setattr(metadata, "run_command", fake_run)

    metadata.delivery_metadata({"metadataTimeoutMinutes": 5}, _state("kimi"), tmp_path)


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


def test_codex_metadata_rejects_command_execution_items(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command: str, arguments: list[str], **kwargs: object) -> CommandResult:
        result_path = Path(arguments[arguments.index("--output-last-message") + 1])
        result_path.write_text(_metadata_text(), encoding="utf-8")
        output = json.dumps(
            {
                "type": "item.completed",
                "item": {"type": "command_execution", "command": "pwd", "exit_code": 0},
            }
        )
        return CommandResult(output, "", 0, f"{command} exec")

    monkeypatch.setattr(metadata, "run_command", fake_run)

    with pytest.raises(ContractError, match="tool event"):
        metadata.delivery_metadata({"metadataTimeoutMinutes": 5}, _state("codex"), tmp_path)


def test_metadata_rejects_tool_events_after_a_structured_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_run(command: str, arguments: list[str], **kwargs: object) -> CommandResult:
        output = "\n".join(
            [
                _metadata_text(),
                json.dumps({"role": "tool", "content": "late tool call"}),
            ]
        )
        return CommandResult(output, "", 0, f"{command} run")

    monkeypatch.setattr(metadata, "run_command", fake_run)

    with pytest.raises(ContractError, match="tool event"):
        metadata.delivery_metadata({"metadataTimeoutMinutes": 5}, _state("opencode"), tmp_path)
