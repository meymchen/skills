from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from fakes import install_python_tool, isolated_agent_environment

from deliver_github_issues.cli import build_parser

PROJECT = Path(__file__).parents[1]


def run_cli(
    *arguments: str, cwd: Path = PROJECT, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "deliver_github_issues.cli", *arguments],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def install_agent_fakes(directory: Path) -> None:
    install_python_tool(
        directory,
        "codex",
        """import sys
args = sys.argv[1:]
print('codex-cli 0.147.0' if args == ['--version'] else 'authenticated')
""",
    )
    install_python_tool(
        directory,
        "claude",
        """import sys
args = sys.argv[1:]
print('2.1.227' if args == ['--version'] else 'authenticated')
""",
    )
    install_python_tool(
        directory,
        "opencode",
        """import json, sys
args = sys.argv[1:]
if args == ['--version']:
    print('1.18.18')
elif args[:2] in (['debug', 'config'], ['debug', 'agent']):
    print(json.dumps({'tools': {'*': False}, 'permission': {'*': 'deny'}}))
elif args[:2] == ['auth', 'list']:
    print('openai')
""",
    )
    install_python_tool(
        directory,
        "kimi",
        """import sys
args = sys.argv[1:]
print('0.35.0' if args == ['--version'] else '--agent-file --skills-dir --output-format')
""",
    )


def test_cli_requires_exactly_one_operation() -> None:
    result = run_cli()

    assert result.returncode == 10
    assert (
        "one of --queue, --issues, --all-ready, --clean-summaries, or --resume is required"
        in result.stderr
    )


def test_instruction_is_only_valid_when_resuming() -> None:
    result = run_cli("--issues", "#14", "--instruction", "try again")

    assert result.returncode == 10
    assert "--instruction requires --resume" in result.stderr


def test_resume_rejects_new_config_preview_and_agent_selection() -> None:
    run_id = "20260813T120000Z-1234abcd"

    configured = run_cli("--resume", run_id, "--config", "other.json")
    previewed = run_cli("--resume", run_id, "--what-if")
    rerouted = run_cli("--resume", run_id, "--primary-agent", "claude")

    assert configured.returncode == 10
    assert "--config cannot be combined with --resume" in configured.stderr
    assert previewed.returncode == 10
    assert "--what-if cannot be combined with --resume" in previewed.stderr
    assert rerouted.returncode == 10
    assert "agent selection cannot be combined with --resume" in rerouted.stderr


def test_agent_options_use_strict_defaults_and_enums() -> None:
    defaults = build_parser().parse_args(["--issues", "#14"])

    assert defaults.primary_agent == "codex"
    assert defaults.metadata_agent == "opencode"
    for provider in ("codex", "claude", "opencode", "kimi"):
        selected = build_parser().parse_args(
            ["--issues", "#14", "--primary-agent", provider, "--metadata-agent", provider]
        )
        assert selected.primary_agent == provider
        assert selected.metadata_agent == provider
    assert run_cli("--issues", "#14", "--metadata-agent", "copilot").returncode == 10


def test_what_if_preserves_queue_order_and_creates_no_state(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    config_dir = tmp_path / ".scratch"
    config_dir.mkdir()
    (config_dir / "deliver-github-issues.json").write_text(
        json.dumps(
            {
                "version": 1,
                "readyLabel": "ready-for-agent",
                "branchPrefix": "agent/issue-",
                "ciTimeoutMinutes": 60,
                "primaryTimeoutMinutes": 60,
                "metadataTimeoutMinutes": 5,
                "maxPrimaryFixAttempts": 3,
                "localChecks": [{"name": "test", "command": "uv", "arguments": ["run", "pytest"]}],
                "requiredChecks": ["test"],
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
    fake_bin = tmp_path / "bin"
    install_agent_fakes(fake_bin)
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
    print(json.dumps({'number':number,'title':f'Issue {number}','body':'body',
        'updatedAt':'2026-08-13T00:00:00Z','state':'OPEN',
        'labels':[{'name':'ready-for-agent'}],
        'blockedBy':{'nodes':[],'totalCount':0},'blocking':{'nodes':[],'totalCount':0}}))
    raise SystemExit(0)
raise SystemExit(2)
""",
    )
    environment = isolated_agent_environment(fake_bin)

    result = run_cli("--queue", str(queue), "--what-if", cwd=tmp_path, env=environment)

    assert result.returncode == 0, result.stderr
    assert result.stdout.index("#82") < result.stdout.index("#79")
    assert "skills=implement,tdd" in result.stdout
    assert not (tmp_path / ".agent-runs").exists()


def test_issue_selector_is_dependency_sorted_with_configured_claude(tmp_path: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=tmp_path, check=True, capture_output=True)
    config_dir = tmp_path / ".scratch"
    config_dir.mkdir()
    policy = {
        "version": 1,
        "readyLabel": "ready-for-agent",
        "branchPrefix": "agent/issue-",
        "ciTimeoutMinutes": 60,
        "primaryTimeoutMinutes": 60,
        "metadataTimeoutMinutes": 5,
        "maxPrimaryFixAttempts": 3,
        "localChecks": [{"name": "test", "command": "uv", "arguments": ["run", "pytest"]}],
        "requiredChecks": ["test"],
    }
    (config_dir / "deliver-github-issues.json").write_text(json.dumps(policy), encoding="utf-8")
    fake_bin = tmp_path / "bin"
    install_agent_fakes(fake_bin)
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
if args[:2] == ['issue', 'list']:
    print(json.dumps([{'number':14},{'number':15},{'number':16}]))
    raise SystemExit(0)
if args[:2] == ['issue', 'view']:
    number = int(args[2])
    blocked = [{'number': 15, 'state': 'OPEN'}] if number == 14 else []
    print(json.dumps({'number':number,'title':f'Issue {number}','body':'body',
        'updatedAt':'2026-08-13T00:00:00Z','state':'OPEN',
        'labels':[{'name':'ready-for-agent'}],
        'blockedBy':{'nodes':blocked,'totalCount':len(blocked)},
        'blocking':{'nodes':[],'totalCount':0}}))
    raise SystemExit(0)
raise SystemExit(2)
""",
    )
    environment = isolated_agent_environment(fake_bin)

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "deliver_github_issues.cli",
            "--issues",
            "#14-16",
            "--primary-agent",
            "claude",
            "--metadata-agent",
            "kimi",
            "--what-if",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.index("#15") < result.stdout.index("#14") < result.stdout.index("#16")
    assert "skills=implement" in result.stdout
    assert "primary=claude" in result.stdout
    assert "metadata=kimi" in result.stdout

    discovered = subprocess.run(
        [
            sys.executable,
            "-m",
            "deliver_github_issues.cli",
            "--all-ready",
            "--what-if",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert discovered.returncode == 0, discovered.stderr
    assert discovered.stdout.index("#15") < discovered.stdout.index("#14")
