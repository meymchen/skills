from __future__ import annotations

from pathlib import Path

import pytest

from deliver_github_issues.contracts import ContractError
from deliver_github_issues.state import load_state, save_state


def state_value() -> dict[str, object]:
    return {
        "version": 1,
        "runId": "20260813T120000Z-1234abcd",
        "repository": "meymchen/lspf",
        "baseBranch": "main",
        "policy": {
            "version": 1,
            "readyLabel": "ready-for-agent",
            "branchPrefix": "agent/issue-",
            "ciTimeoutMinutes": 60,
            "localChecks": [{"name": "test", "command": "uv", "arguments": ["run", "pytest"]}],
            "requiredChecks": ["test"],
            "primaryTimeoutMinutes": 60,
            "metadataTimeoutMinutes": 5,
            "maxPrimaryFixAttempts": 3,
        },
        "agents": {"primary": "codex", "metadata": "opencode", "versions": {}},
        "keepRunSummary": False,
        "issues": [
            {
                "number": 79,
                "skills": ["implement", "tdd"],
                "instruction": "",
                "bodyHash": "a" * 64,
            }
        ],
        "completedIssues": [],
        "index": 0,
        "phase": "prepare",
        "current": None,
        "updatedAt": "2026-08-13T12:00:00Z",
    }


def test_state_is_atomically_saved_and_loaded(tmp_path: Path) -> None:
    path = tmp_path / "state.json"

    save_state(state_value(), path)

    assert load_state(path) == state_value()
    assert not (tmp_path / "state.json.tmp").exists()


@pytest.mark.parametrize("primary", ("codex", "claude", "opencode", "kimi"))
@pytest.mark.parametrize("metadata", ("codex", "claude", "opencode", "kimi"))
def test_state_preserves_every_agent_route(tmp_path: Path, primary: str, metadata: str) -> None:
    state = state_value()
    state["agents"] = {"primary": primary, "metadata": metadata, "versions": {}}
    path = tmp_path / "state.json"

    save_state(state, path)

    assert load_state(path)["agents"] == state["agents"]


def test_state_rejects_unknown_properties(tmp_path: Path) -> None:
    state = state_value()
    state["legacy"] = True
    path = tmp_path / "state.json"

    with pytest.raises(ContractError, match="Additional properties are not allowed"):
        save_state(state, path)


def test_state_rejects_partial_current_issue(tmp_path: Path) -> None:
    state = state_value()
    state["current"] = {"number": 79}

    with pytest.raises(ContractError, match="is a required property"):
        save_state(state, tmp_path / "state.json")


def test_state_rejects_incomplete_nested_checkbox(tmp_path: Path) -> None:
    state = state_value()
    state["phase"] = "implement"
    state["current"] = {
        "number": 79,
        "title": "Do thing",
        "branch": "agent/issue-79",
        "baseSha": "a" * 40,
        "fixAttempts": 0,
        "issueUpdatedAt": "2026-08-13T00:00:00Z",
        "issueUrl": "https://github.test/issues/79",
        "checkboxes": [{"text": "works"}],
        "testedSha": None,
        "implementation": None,
        "review": None,
        "localChecks": [],
        "ciChecks": [],
        "prNumber": None,
        "prUrl": None,
        "audit": None,
        "metadata": None,
    }

    with pytest.raises(ContractError, match="is a required property"):
        save_state(state, tmp_path / "state.json")
