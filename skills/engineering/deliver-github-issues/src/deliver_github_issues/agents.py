from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from deliver_github_issues.commands import CommandError, run_command
from deliver_github_issues.contracts import ContractError, schema_path, validate_contract


def invoke_agent_phase(
    phase: str,
    policy: dict[str, Any],
    state: dict[str, Any],
    queue_item: dict[str, Any],
    issue: dict[str, Any],
    run_dir: Path,
    root: Path,
) -> dict[str, Any]:
    current = state["current"]
    number = current["number"]
    prompt_path = run_dir / f"{number}-{phase}-prompt.txt"
    result_path = run_dir / f"{number}-{phase}-result.json"
    events_path = run_dir / f"{number}-{phase}-events.jsonl"
    schema = schema_path(phase)
    skill_calls = [f"${skill}" for skill in queue_item["skills"]]
    payload = {
        "phase": phase,
        "skills": skill_calls,
        "instruction": queue_item["instruction"],
        "headSha": current["testedSha"],
        "issue": issue,
        "originalCheckboxes": current["checkboxes"],
        "localChecks": current["localChecks"],
        "ciChecks": current["ciChecks"],
    }
    prompt = (
        "This is a worker phase of a delivery workflow the user already invoked manually. "
        "Do not invoke the manual-only deliver-github-issues skill. "
        f"Invoke required implementation skills in this order: {', '.join(skill_calls)}. "
        "You may also invoke any other installed and enabled skill relevant to the issue; "
        "report every skill actually used. Ignore unavailable or disabled optional skills "
        f"unless the issue explicitly requires one. Follow the {phase} contract: implement "
        "may edit the workspace and run targeted tests; audit must keep it read-only and "
        "classify every supplied checkbox. Return only the requested schema object.\nInput:\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
    prompt_path.write_text(prompt, encoding="utf-8", newline="\n")
    provider = policy["primaryAgent"]["provider"]
    model = policy["primaryAgent"]["model"]
    if provider == "codex":
        sandbox = "read-only" if phase == "audit" else "workspace-write"
        arguments = [
            "exec",
            "--ephemeral",
            "--sandbox",
            sandbox,
            "--json",
            "--output-schema",
            str(schema),
            "--output-last-message",
            str(result_path),
            "-C",
            str(root),
        ]
        if model:
            arguments.extend(["--model", model])
        arguments.append("-")
        result = run_command("codex", arguments, cwd=root, input_text=prompt, allow_failure=True)
        events_path.write_text(
            result.output + (("\n" + result.stderr) if result.stderr else ""),
            encoding="utf-8",
            newline="\n",
        )
        if result.exit_code:
            raise CommandError(f"Codex {phase} failed with exit code {result.exit_code}.")
    else:
        permission_mode = "plan" if phase == "audit" else "acceptEdits"
        allowed_tools = "Read,Glob,Grep" if phase == "audit" else "Read,Edit,Write,Glob,Grep,Bash"
        schema_text = schema.read_text(encoding="utf-8")
        arguments = [
            "--print",
            "--no-session-persistence",
            "--output-format",
            "json",
        ]
        if model:
            arguments.extend(["--model", model])
        arguments.extend(
            [
                "--json-schema",
                schema_text,
                "--permission-mode",
                permission_mode,
                "--allowedTools",
                allowed_tools,
            ]
        )
        result = run_command("claude", arguments, cwd=root, input_text=prompt, allow_failure=True)
        events_path.write_text(
            result.output + (("\n" + result.stderr) if result.stderr else ""),
            encoding="utf-8",
            newline="\n",
        )
        if result.exit_code:
            raise CommandError(f"Claude {phase} failed with exit code {result.exit_code}.")
        try:
            envelope = json.loads(result.output)
            structured = envelope["structured_output"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise ContractError(
                f"Claude {phase} produced invalid structured JSON: {error}"
            ) from error
        result_path.write_text(
            json.dumps(structured, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    if not result_path.is_file():
        raise ContractError(f"{provider} {phase} produced no structured result.")
    try:
        value = json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ContractError(f"{provider} {phase} result is invalid JSON: {error}") from error
    validate_contract(value, phase)
    return value
