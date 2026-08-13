from __future__ import annotations

import json
from pathlib import Path

import pytest

from deliver_github_issues.contracts import ContractError, load_policy, load_queue
from deliver_github_issues.selection import SelectionError, parse_issue_selector


def write_json(path: Path, value: object) -> Path:
    path.write_text(json.dumps(value), encoding="utf-8")
    return path


def valid_queue() -> dict[str, object]:
    return {
        "version": 1,
        "repository": "meymchen/lspf",
        "baseBranch": "main",
        "issues": [{"number": 79, "skills": ["tdd"], "instruction": ""}],
    }


def valid_policy() -> dict[str, object]:
    return {
        "version": 1,
        "readyLabel": "ready-for-agent",
        "branchPrefix": "agent/issue-",
        "ciTimeoutMinutes": 60,
        "localChecks": [{"name": "test", "command": "uv", "arguments": ["run", "pytest"]}],
        "requiredChecks": ["test"],
        "primaryAgent": {"provider": "codex", "model": ""},
        "metadataAgent": {"provider": "deterministic", "model": "", "fallback": True},
    }


def test_queue_rejects_duplicate_issues_before_use(tmp_path: Path) -> None:
    queue = valid_queue()
    queue["issues"] = [
        {"number": 79, "skills": [], "instruction": ""},
        {"number": 79, "skills": [], "instruction": ""},
    ]

    with pytest.raises(ContractError, match="Duplicate issue number: 79"):
        load_queue(write_json(tmp_path / "queue.json", queue))


def test_queue_prepends_implement_once(tmp_path: Path) -> None:
    queue = load_queue(write_json(tmp_path / "queue.json", valid_queue()))

    assert queue["issues"][0]["skills"] == ["implement", "tdd"]


def test_policy_rejects_unknown_fields(tmp_path: Path) -> None:
    policy = valid_policy()
    policy["shell"] = True

    with pytest.raises(ContractError, match="Additional properties are not allowed"):
        load_policy(write_json(tmp_path / "policy.json", policy))


def test_policy_rejects_duplicate_local_check_names(tmp_path: Path) -> None:
    policy = valid_policy()
    policy["localChecks"] = [policy["localChecks"][0], policy["localChecks"][0]]

    with pytest.raises(ContractError, match="Duplicate local check name: test"):
        load_policy(write_json(tmp_path / "policy.json", policy))


def test_copilot_metadata_rejects_explicit_model(tmp_path: Path) -> None:
    policy = valid_policy()
    policy["metadataAgent"] = {"provider": "copilot", "model": "gpt-5", "fallback": False}

    with pytest.raises(ContractError, match="Copilot CLI does not expose"):
        load_policy(write_json(tmp_path / "policy.json", policy))


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        ("14", [14]),
        ("#14", [14]),
        ("14-16", [14, 15, 16]),
        ("#14-#16, 15, #18", [14, 15, 16, 18]),
    ],
)
def test_issue_selector_accepts_documented_flexible_forms(
    selector: str, expected: list[int]
) -> None:
    assert parse_issue_selector(selector) == expected


def test_issue_selector_rejects_oversized_ranges() -> None:
    with pytest.raises(SelectionError, match="at most 500"):
        parse_issue_selector("1-501")
