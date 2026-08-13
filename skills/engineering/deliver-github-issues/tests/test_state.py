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
            "primaryAgent": {"provider": "codex", "model": ""},
            "metadataAgent": {"provider": "deterministic", "model": "", "fallback": True},
        },
        "issues": [{"number": 79, "skills": ["implement", "tdd"], "instruction": ""}],
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
