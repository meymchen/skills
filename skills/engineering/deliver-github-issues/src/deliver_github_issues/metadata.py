from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from deliver_github_issues.commands import CommandError, run_command
from deliver_github_issues.contracts import ContractError, validate_contract


def _assert_metadata(metadata: dict[str, Any], issue_number: int) -> None:
    validate_contract(metadata, "metadata")
    for field in ("commitTitle", "prTitle"):
        value = metadata[field]
        if "\n" in value or "\r" in value or not value.endswith(f"(#{issue_number})"):
            raise ContractError(f"Metadata {field} must be one line ending with (#{issue_number}).")


def _has_tool_event(event: dict[str, Any]) -> bool:
    event_type = str(event.get("type", "")).lower()
    if "tool" in event_type or str(event.get("role", "")).lower() == "tool":
        return True
    if any(event.get(key) for key in ("tool_calls", "toolCalls", "tool_call", "toolCall")):
        return True
    return any(
        _has_tool_event(nested)
        for key in ("part", "message", "data")
        if isinstance((nested := event.get(key)), dict)
    )


def _text_candidates(event: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    part = event.get("part")
    if isinstance(part, dict) and isinstance(part.get("text"), str):
        candidates.append(part["text"])
    if isinstance(event.get("content"), str):
        candidates.append(event["content"])
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
            return event
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
        "Return only a JSON object with string fields commitTitle, prTitle, and summary. "
        f"Both titles must be one line, at most 200 characters, and end with "
        f"(#{current['number']}). Only restate facts in the supplied data.\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )


def _opencode_environment() -> dict[str, str]:
    environment = os.environ.copy()
    deny_all = {"tools": {"*": False}, "permission": {"*": "deny"}}
    config = {
        **deny_all,
        "autoupdate": False,
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
    return environment


def _prepare_kimi_home(target: Path) -> None:
    source = Path(os.environ.get("KIMI_CODE_HOME", Path.home() / ".kimi"))
    target.mkdir(parents=True, exist_ok=True)
    credentials = source / "credentials"
    if credentials.is_dir():
        shutil.copytree(credentials, target / credentials.name)


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
        env=_opencode_environment(),
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
        if provider == "opencode":
            output = _run_opencode(prompt, temporary, timeout_seconds, log_path)
        elif provider == "kimi":
            output = _run_kimi(prompt, temporary, timeout_seconds, log_path)
        else:
            raise ContractError(f"Unsupported metadata provider: {provider}")
        metadata = _metadata_from_events(output, provider)
    _assert_metadata(metadata, state["current"]["number"])
    return metadata
