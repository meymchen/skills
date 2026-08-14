from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from deliver_github_issues.commands import CommandError, run_command
from deliver_github_issues.contracts import ContractError, schema_path, validate_contract


def _assert_metadata(metadata: dict[str, Any], issue_number: int) -> None:
    validate_contract(metadata, "metadata")
    for field in ("commitTitle", "prTitle"):
        value = metadata[field]
        if "\n" in value or "\r" in value or not value.endswith(f"(#{issue_number})"):
            raise ContractError(f"Metadata {field} must be one line ending with (#{issue_number}).")


def _has_tool_event(event: dict[str, Any]) -> bool:
    event_type = str(event.get("type", "")).lower()
    forbidden_types = {
        "command_execution",
        "file_change",
        "mcp_tool_call",
        "collab_tool_call",
        "web_search",
    }
    if (
        "tool" in event_type
        or event_type in forbidden_types
        or str(event.get("role", "")).lower() == "tool"
    ):
        return True
    if any(event.get(key) for key in ("tool_calls", "toolCalls", "tool_call", "toolCall")):
        return True
    for nested in event.values():
        if isinstance(nested, dict) and _has_tool_event(nested):
            return True
        if isinstance(nested, list) and any(
            isinstance(item, dict) and _has_tool_event(item) for item in nested
        ):
            return True
    return False


def _text_candidates(event: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    part = event.get("part")
    if isinstance(part, dict) and isinstance(part.get("text"), str):
        candidates.append(part["text"])
    if isinstance(event.get("content"), str):
        candidates.append(event["content"])
    if isinstance(event.get("result"), str):
        candidates.append(event["result"])
    message = event.get("message")
    if isinstance(message, dict) and isinstance(message.get("content"), str):
        candidates.append(message["content"])
    data = event.get("data")
    if isinstance(data, dict):
        candidates.extend(_text_candidates(data))
    return candidates


def _metadata_from_events(output: str, provider: str) -> dict[str, Any]:
    candidates: list[str] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            raise ContractError(
                f"{provider} metadata stream contains invalid JSON: {error}"
            ) from error
        if not isinstance(event, dict):
            raise ContractError(f"{provider} metadata stream contains a non-object event.")
        if _has_tool_event(event):
            raise ContractError(f"{provider} metadata stream contains a forbidden tool event.")
        if set(event) == {"commitTitle", "prTitle", "summary"}:
            candidates.append(json.dumps(event))
        if isinstance(event.get("structured_output"), dict):
            candidates.append(json.dumps(event["structured_output"]))
        candidates.extend(_text_candidates(event))
    for candidate in reversed(candidates):
        try:
            value = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise ContractError(f"{provider} metadata stream produced no JSON object response.")


def _metadata_prompt(state: dict[str, Any]) -> str:
    current = state["current"]
    successful = [check["command"] for check in current["localChecks"] if check["exitCode"] == 0]
    payload = {
        "issue": {"number": current["number"], "title": current["title"]},
        "verifiedImplementationSummary": current["implementation"]["summary"],
        "successfulChecks": successful,
    }
    return (
        # Keep the prompt single-line: on Windows the opencode npm shim is a
        # batch file, and multi-line argv does not reach the model intact.
        "You have no tools available; do not attempt any tool calls. "
        "Return only a JSON object with string fields commitTitle, prTitle, and summary. "
        f"Both titles must be one line, at most 200 characters, and end with "
        f"(#{current['number']}). Only restate facts in the supplied data. "
        + json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    )


_PINNED_METADATA_MODEL = "deepseek/deepseek-v4-flash"


def _opencode_configured_model() -> str:
    environment = os.environ.copy()
    environment["OPENCODE_DISABLE_MODELS_FETCH"] = "true"
    try:
        result = run_command(
            "opencode",
            ["debug", "config"],
            allow_failure=True,
            timeout_seconds=60,
            env=environment,
        )
    except CommandError:
        return _PINNED_METADATA_MODEL
    if result.exit_code == 0:
        try:
            value = json.loads(result.output)
        except json.JSONDecodeError:
            value = None
        model = value.get("model") if isinstance(value, dict) else None
        if isinstance(model, str) and model.strip():
            return model.strip()
    return _PINNED_METADATA_MODEL


def _opencode_environment(config_home: Path | None = None) -> dict[str, str]:
    environment = os.environ.copy()
    deny_all = {"tools": {"*": False}, "permission": {"*": "deny"}}
    config = {
        **deny_all,
        "autoupdate": False,
        # The metadata agent's XDG_CONFIG_HOME is redirected to a temporary
        # directory, so opencode's own config is invisible to it. Resolve the
        # user's effective model up front and inject it; when nothing is
        # configured, fall back to a pinned model capable of the strict
        # no-tools JSON contract.
        "model": _opencode_configured_model(),
        "agent": {
            "metadata": {
                "description": "Generate verified delivery metadata without tools.",
                **deny_all,
            }
        },
    }
    environment.update(
        {
            "OPENCODE_CONFIG_CONTENT": json.dumps(config, separators=(",", ":")),
            "OPENCODE_PERMISSION": json.dumps({"*": "deny"}, separators=(",", ":")),
            "OPENCODE_DISABLE_PROJECT_CONFIG": "true",
            "OPENCODE_DISABLE_DEFAULT_PLUGINS": "true",
            "OPENCODE_DISABLE_CLAUDE_CODE": "true",
            "OPENCODE_DISABLE_AUTOUPDATE": "true",
            "OPENCODE_DISABLE_MODELS_FETCH": "true",
        }
    )
    if config_home is not None:
        config_home.mkdir(parents=True, exist_ok=True)
        environment["XDG_CONFIG_HOME"] = str(config_home)
    return environment


def _prepare_kimi_home(target: Path) -> None:
    source = Path(os.environ.get("KIMI_CODE_HOME", Path.home() / ".kimi-code"))
    target.mkdir(parents=True, exist_ok=True)
    config = source / "config.toml"
    if config.is_file():
        shutil.copy2(config, target / config.name)
    credentials = source / "credentials"
    if credentials.is_dir():
        shutil.copytree(credentials, target / credentials.name)


def _codex_metadata_arguments(temporary: Path) -> list[str]:
    return [
        "exec",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--ignore-user-config",
        "--ignore-rules",
        "--skip-git-repo-check",
        "--json",
        "-c",
        "mcp_servers={}",
        "-C",
        str(temporary),
    ]


def _claude_metadata_arguments() -> list[str]:
    return [
        "--print",
        "--no-session-persistence",
        "--safe-mode",
        "--tools",
        "",
        "--permission-mode",
        "plan",
        "--output-format",
        "stream-json",
        "--verbose",
    ]


def _run_codex(prompt: str, temporary: Path, timeout_seconds: int, log_path: Path) -> str:
    result_path = temporary / "metadata-result.json"
    result = run_command(
        "codex",
        [
            *_codex_metadata_arguments(temporary),
            "--output-schema",
            str(schema_path("metadata")),
            "--output-last-message",
            str(result_path),
            "-",
        ],
        cwd=temporary,
        input_text=prompt,
        log_path=log_path,
        allow_failure=True,
        timeout_seconds=timeout_seconds,
        transient_retries=1,
    )
    if result.exit_code:
        raise CommandError(f"codex metadata agent failed ({result.exit_code}).")
    if not result_path.is_file():
        raise ContractError("codex metadata agent produced no structured result.")
    return result.output + "\n" + result_path.read_text(encoding="utf-8")


def _run_claude(prompt: str, temporary: Path, timeout_seconds: int, log_path: Path) -> str:
    result = run_command(
        "claude",
        [
            *_claude_metadata_arguments(),
            "--json-schema",
            schema_path("metadata").read_text(encoding="utf-8"),
        ],
        cwd=temporary,
        input_text=prompt,
        log_path=log_path,
        allow_failure=True,
        timeout_seconds=timeout_seconds,
        transient_retries=1,
    )
    if result.exit_code:
        raise CommandError(f"claude metadata agent failed ({result.exit_code}).")
    return result.output


def _run_opencode(prompt: str, temporary: Path, timeout_seconds: int, log_path: Path) -> str:
    result = run_command(
        "opencode",
        [
            "run",
            "--pure",
            "--dir",
            str(temporary),
            "--format",
            "json",
            "--agent",
            "metadata",
            prompt,
        ],
        cwd=temporary,
        env=_opencode_environment(temporary / "xdg-config"),
        log_path=log_path,
        allow_failure=True,
        timeout_seconds=timeout_seconds,
        transient_retries=1,
    )
    if result.exit_code:
        raise CommandError(f"opencode metadata agent failed ({result.exit_code}).")
    return result.output


def _run_kimi(prompt: str, temporary: Path, timeout_seconds: int, log_path: Path) -> str:
    agent_path = temporary / "metadata-agent.md"
    agent_path.write_text(
        "---\nname: metadata\ndescription: Generate verified delivery metadata.\n"
        "tools: []\nsubagents: []\n---\n"
        "Return only the requested metadata JSON. Do not use tools or delegate.\n",
        encoding="utf-8",
        newline="\n",
    )
    empty_skills = temporary / "empty-skills"
    empty_skills.mkdir()
    kimi_home = temporary / "kimi-home"
    _prepare_kimi_home(kimi_home)
    environment = os.environ.copy()
    environment.update(
        {
            "KIMI_CODE_HOME": str(kimi_home),
            "KIMI_CODE_EXPERIMENTAL_FLAG": "1",
            "KIMI_DISABLE_TELEMETRY": "1",
            "KIMI_CODE_NO_AUTO_UPDATE": "1",
        }
    )
    result = run_command(
        "kimi",
        [
            "-p",
            prompt,
            "--agent-file",
            str(agent_path),
            "--skills-dir",
            str(empty_skills),
            "--output-format",
            "stream-json",
        ],
        cwd=temporary,
        env=environment,
        log_path=log_path,
        allow_failure=True,
        timeout_seconds=timeout_seconds,
        transient_retries=1,
    )
    if result.exit_code:
        raise CommandError(f"kimi metadata agent failed ({result.exit_code}).")
    return result.output


def delivery_metadata(
    policy: dict[str, Any], state: dict[str, Any], run_dir: Path
) -> dict[str, Any]:
    provider = state["agents"]["metadata"]
    prompt = _metadata_prompt(state)
    log_path = run_dir / f"{state['current']['number']}-metadata-agent.log"
    with tempfile.TemporaryDirectory(prefix="deliver-metadata-") as temporary_name:
        temporary = Path(temporary_name)
        timeout_seconds = policy["metadataTimeoutMinutes"] * 60
        metadata: dict[str, Any] | None = None
        extraction_error: ContractError | None = None
        for attempt in range(2):
            if attempt:
                prompt += (
                    " REMINDER: you have no tools; do not attempt tool calls. Reply with "
                    "exactly one raw JSON object containing commitTitle, prTitle, and summary "
                    "— no prose, no markdown code fence."
                )
            if provider == "codex":
                output = _run_codex(prompt, temporary, timeout_seconds, log_path)
            elif provider == "claude":
                output = _run_claude(prompt, temporary, timeout_seconds, log_path)
            elif provider == "opencode":
                output = _run_opencode(prompt, temporary, timeout_seconds, log_path)
            elif provider == "kimi":
                output = _run_kimi(prompt, temporary, timeout_seconds, log_path)
            else:
                raise ContractError(f"Unsupported metadata provider: {provider}")
            try:
                metadata = _metadata_from_events(output, provider)
                break
            except ContractError as error:
                extraction_error = error
        if metadata is None:
            raise extraction_error or ContractError(
                f"{provider} metadata produced no structured result."
            )
    _assert_metadata(metadata, state["current"]["number"])
    return metadata
