from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from fakes import install_python_tool

PROJECT = Path(__file__).parents[1]


def run_cli(*arguments: str, cwd: Path = PROJECT) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "deliver_github_issues.cli", *arguments],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_requires_exactly_one_operation() -> None:
    result = run_cli()

    assert result.returncode == 10
    assert "one of --queue, --issues, or --resume is required" in result.stderr


def test_instruction_is_only_valid_when_resuming() -> None:
    result = run_cli("--issues", "#14", "--instruction", "try again")

    assert result.returncode == 10
    assert "--instruction requires --resume" in result.stderr


def test_resume_rejects_new_config_and_what_if() -> None:
    run_id = "20260813T120000Z-1234abcd"

    configured = run_cli("--resume", run_id, "--config", "other.json")
    previewed = run_cli("--resume", run_id, "--what-if")

    assert configured.returncode == 10
    assert "--config cannot be combined with --resume" in configured.stderr
    assert previewed.returncode == 10
    assert "--what-if cannot be combined with --resume" in previewed.stderr


def test_what_if_preserves_queue_order_and_creates_no_state(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    config_dir = tmp_path / ".github"
    config_dir.mkdir()
    (config_dir / "deliver-github-issues.json").write_text(
        json.dumps(
            {
                "version": 1,
                "readyLabel": "ready-for-agent",
                "branchPrefix": "agent/issue-",
                "ciTimeoutMinutes": 60,
                "localChecks": [{"name": "test", "command": "uv", "arguments": ["run", "pytest"]}],
                "requiredChecks": ["test"],
                "primaryAgent": {"provider": "codex", "model": ""},
                "metadataAgent": {
                    "provider": "deterministic",
                    "model": "",
                    "fallback": True,
                },
            }
        ),
        encoding="utf-8",
    )
    queue = tmp_path.parent / "ordered.json"
    queue.write_text(
        json.dumps(
            {
                "version": 1,
                "repository": "meymchen/lspf",
                "baseBranch": "main",
                "issues": [
                    {"number": 82, "skills": [], "instruction": ""},
                    {"number": 79, "skills": ["tdd"], "instruction": "test first"},
                ],
            }
        ),
        encoding="utf-8",
    )

    result = run_cli("--queue", str(queue), "--what-if", cwd=tmp_path)

    assert result.returncode == 0, result.stderr
    assert result.stdout.index("#82") < result.stdout.index("#79")
    assert "skills=implement,tdd" in result.stdout
    assert not (tmp_path / ".agent-runs").exists()


def test_issue_selector_is_dependency_sorted_with_configured_claude(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    config_dir = tmp_path / ".github"
    config_dir.mkdir()
    policy = {
        "version": 1,
        "readyLabel": "ready-for-agent",
        "branchPrefix": "agent/issue-",
        "ciTimeoutMinutes": 60,
        "localChecks": [{"name": "test", "command": "uv", "arguments": ["run", "pytest"]}],
        "requiredChecks": ["test"],
        "primaryAgent": {"provider": "claude", "model": "sonnet"},
        "metadataAgent": {"provider": "deterministic", "model": "", "fallback": True},
    }
    (config_dir / "deliver-github-issues.json").write_text(json.dumps(policy), encoding="utf-8")
    fake_bin = tmp_path / "bin"
    install_python_tool(
        fake_bin,
        "gh",
        """import json, sys
args = sys.argv[1:]
if args[:2] == ['auth', 'status']:
    raise SystemExit(0)
if args[:2] == ['repo', 'view']:
    print(json.dumps({'nameWithOwner':'meymchen/lspf','defaultBranchRef':{'name':'main'}}))
    raise SystemExit(0)
if args[:2] == ['issue', 'view']:
    number = int(args[2])
    blocked = [{'number': 15, 'state': 'OPEN'}] if number == 14 else []
    print(json.dumps({'number':number,'title':f'Issue {number}','state':'OPEN',
        'labels':[{'name':'ready-for-agent'}],
        'blockedBy':{'nodes':blocked,'totalCount':len(blocked)},
        'blocking':{'nodes':[],'totalCount':0}}))
    raise SystemExit(0)
raise SystemExit(2)
""",
    )
    environment = os.environ.copy()
    environment["PATH"] = str(fake_bin) + os.pathsep + environment["PATH"]

    result = subprocess.run(
        [sys.executable, "-m", "deliver_github_issues.cli", "--issues", "#14-16", "--what-if"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.index("#15") < result.stdout.index("#14") < result.stdout.index("#16")
    assert "skills=implement" in result.stdout
    assert "codex" not in result.stderr.lower()
