from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from deliver_github_issues.contracts import ContractError, validate_contract


def validate_state(state: dict[str, Any]) -> None:
    validate_contract(state, "state")
    validate_contract(state["policy"], "repository")
    validate_contract(
        {
            "version": 1,
            "repository": state["repository"],
            "baseBranch": state["baseBranch"],
            "issues": state["issues"],
        },
        "queue",
    )
    current = state["current"]
    if current and current.get("implementation") is not None:
        validate_contract(current["implementation"], "implement")
    if current and current.get("audit") is not None:
        validate_contract(current["audit"], "audit")
    if current and current.get("metadata") is not None:
        validate_contract(current["metadata"], "metadata")


def load_state(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ContractError(f"Run state does not exist: {path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"Run state is not valid JSON: {error}") from error
    validate_state(value)
    return value


def save_state(state: dict[str, Any], path: Path) -> None:
    validate_state(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(state, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
