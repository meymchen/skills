from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

from deliver_github_issues.commands import CommandError, command_json, run_command
from deliver_github_issues.contracts import ContractError, schema_path, validate_contract


def deterministic_metadata(state: dict[str, Any]) -> dict[str, str]:
    current = state["current"]
    title = " ".join(current["title"].split()) + f" (#{current['number']})"
    return {
        "commitTitle": title,
        "prTitle": title,
        "summary": current["implementation"]["summary"],
    }


def _assert_metadata(metadata: dict[str, Any], issue_number: int) -> None:
    validate_contract(metadata, "metadata")
    for field in ("commitTitle", "prTitle"):
        value = metadata[field]
        if "\n" in value or "\r" in value or not value.endswith(f"(#{issue_number})"):
            raise ContractError(f"Metadata {field} must be one line ending with (#{issue_number}).")


def delivery_metadata(
    policy: dict[str, Any], state: dict[str, Any], run_dir: Path
) -> dict[str, Any]:
    fallback = deterministic_metadata(state)
    agent = policy["metadataAgent"]
    provider = agent["provider"]
    if provider == "deterministic":
        return fallback
    current = state["current"]
    prompt = (
        "Return only compact JSON with string fields commitTitle, prTitle, and summary.\n"
        f"Both titles must end with (#{current['number']}), contain no newline, and be at most 200 characters.\n"
        "Write a concise factual summary. Do not use tools or inspect files.\n"
        f"Issue title: {current['title']}\n"
        f"Implementation summary: {current['implementation']['summary']}\n"
        "Successful checks: " + "; ".join(check["command"] for check in current["localChecks"])
    )
    log_path = run_dir / "metadata-agent.log"
    try:
        with tempfile.TemporaryDirectory(prefix="deliver-metadata-") as temporary_name:
            temporary = Path(temporary_name)
            model = agent["model"]
            if provider == "codex":
                result_path = temporary / "result.json"
                arguments = [
                    "exec",
                    "--ephemeral",
                    "--sandbox",
                    "workspace-write",
                    "--skip-git-repo-check",
                    "--output-schema",
                    str(schema_path("metadata")),
                    "--output-last-message",
                    str(result_path),
                ]
                if model:
                    arguments.extend(["--model", model])
                arguments.append(prompt)
                result = run_command(
                    "codex", arguments, cwd=temporary, log_path=log_path, allow_failure=True
                )
                if result.exit_code or not result_path.is_file():
                    raise CommandError(f"codex metadata agent failed ({result.exit_code}).")
                metadata = json.loads(result_path.read_text(encoding="utf-8"))
            else:
                if provider == "opencode":
                    arguments = ["run"] + (["--model", model] if model else []) + [prompt]
                elif provider == "copilot":
                    arguments = [
                        "--prompt",
                        prompt,
                        "--stream",
                        "off",
                        "--sandbox",
                        "on",
                        "--deny-tool",
                        "*",
                    ]
                else:
                    arguments = ["-p", prompt, "--output-format", "text"] + (
                        ["--model", model] if model else []
                    )
                result = run_command(
                    provider, arguments, cwd=temporary, log_path=log_path, allow_failure=True
                )
                if result.exit_code:
                    raise CommandError(f"{provider} metadata agent failed ({result.exit_code}).")
                metadata = command_json(result, f"{provider} metadata agent")
            _assert_metadata(metadata, current["number"])
            return metadata
    except (CommandError, ContractError, OSError, json.JSONDecodeError) as error:
        with log_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(f"Fallback: {error}\n")
        if not agent["fallback"]:
            raise
        return fallback
