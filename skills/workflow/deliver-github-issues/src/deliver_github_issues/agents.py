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
    provider = state["agents"]["primary"]
    marker = "$" if provider == "codex" else "/"
    skill_calls = [f"{marker}{skill}" for skill in queue_item["skills"]]
    additional_skill_calls = [
        f"{marker}{skill}" for skill in queue_item["skills"] if skill != "implement"
    ]
    phase_skills = (
        skill_calls
        if phase == "implement"
        else ([f"{marker}code-review"] if phase == "review" else [])
    )
    payload = {
        "phase": phase,
        "skills": phase_skills,
        "instruction": queue_item["instruction"] if phase == "implement" else "",
        "headSha": current["testedSha"],
        "issue": issue,
        "originalCheckboxes": current["checkboxes"],
        "localChecks": current["localChecks"],
        "ciChecks": current["ciChecks"],
    }
    if phase == "implement":
        invocation = f"{marker}implement {current['issueUrl']}"
    elif phase == "review":
        invocation = f"{marker}code-review {current['baseSha']}"
    else:
        invocation = f"Acceptance audit for {current['issueUrl']}"
    if phase == "implement":
        skill_instruction = (
            "The first line is the single implement invocation. Within that workflow, invoke "
            f"additional required skills in this order: {', '.join(additional_skill_calls)}. "
            if additional_skill_calls
            else "The first line is the single implement invocation. "
        )
        skill_instruction += (
            "You may also invoke other installed skills relevant to implementation and must "
            "report every skill actually used. "
        )
    elif phase == "review":
        skill_instruction = "Invoke only code-review; do not rerun implementation skills. "
    else:
        skill_instruction = "Do not invoke implementation skills during the acceptance audit. "
    prompt = (
        invocation
        + "\n\n"
        + (
            "This is a worker phase of a delivery workflow the user already invoked manually. "
            "Do not invoke the manual-only deliver-github-issues skill. "
            + skill_instruction
            + f"Follow the {phase} contract: implement "
            "may edit the workspace, create provisional local commits, and run targeted tests; "
            "review and audit must keep it read-only; review reports every finding and audit must "
            "classify every supplied checkbox. Return only the requested schema object.\nInput:\n"
            + json.dumps(payload, ensure_ascii=False, indent=2)
        )
    )
    if phase == "implement":
        prompt = prompt.replace(
            "\nInput:\n",
            " Do not push, create or edit a pull request, or modify the issue. Do not create "
            "or switch branches, and do not use destructive git commands. Confirm the issue "
            "number and title before editing. Commit before the final code-review so the review "
            "can inspect the full diff. Return control to the workflow after the structured "
            "handoff.\nInput:\n",
        )
    prompt_path.write_text(prompt, encoding="utf-8", newline="\n")
    if provider == "codex":
        sandbox = "read-only" if phase in {"audit", "review"} else "workspace-write"
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
        arguments.append("-")
        result = run_command(
            "codex",
            arguments,
            cwd=root,
            input_text=prompt,
            allow_failure=True,
            timeout_seconds=policy["primaryTimeoutMinutes"] * 60,
            transient_retries=1,
        )
        events_path.write_text(
            result.output + (("\n" + result.stderr) if result.stderr else ""),
            encoding="utf-8",
            newline="\n",
        )
        if result.exit_code:
            raise CommandError(f"Codex {phase} failed with exit code {result.exit_code}.")
    else:
        settings_path = run_dir / "claude-sandbox-settings.json"
        settings_path.write_text(
            json.dumps(
                {
                    "sandbox": {
                        "enabled": True,
                        "failIfUnavailable": True,
                        "autoAllowBashIfSandboxed": True,
                        "allowUnsandboxedCommands": False,
                        "excludedCommands": [],
                        "filesystem": {"denyWrite": [str(run_dir)]},
                        "network": {"allowedDomains": [], "deniedDomains": ["*"]},
                    }
                }
            ),
            encoding="utf-8",
        )
        permission_mode = "plan" if phase in {"audit", "review"} else "acceptEdits"
        allowed_tools = (
            "Read,Glob,Grep,Bash(git diff:*),Bash(git log:*),Bash(git show:*)"
            if phase in {"audit", "review"}
            else "Read,Edit,Write,Glob,Grep,Bash"
        )
        schema_text = schema.read_text(encoding="utf-8")
        arguments = [
            "--print",
            "--no-session-persistence",
            "--settings",
            str(settings_path),
            "--output-format",
            "json",
        ]
        arguments.extend(
            [
                "--json-schema",
                schema_text,
                "--permission-mode",
                permission_mode,
                "--allowedTools",
                allowed_tools,
                "--disallowedTools",
                "Bash(gh:*),Bash(git push:*),Bash(git * push:*),Bash(git branch:*),Bash(git switch:*),Bash(git checkout:*),Bash(git reset:*),Bash(git clean:*),Bash(git worktree:*),Bash(git merge:*),Bash(git rebase:*),Bash(git tag:*),Bash(git remote:*)",
            ]
        )
        result = run_command(
            "claude",
            arguments,
            cwd=root,
            input_text=prompt,
            allow_failure=True,
            timeout_seconds=policy["primaryTimeoutMinutes"] * 60,
            transient_retries=1,
        )
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
