from __future__ import annotations

import json
from copy import deepcopy
from importlib.resources import files
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator


class ContractError(ValueError):
    """An input or output does not satisfy a workflow contract."""


def _read_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ContractError(f"{label} file does not exist: {path}") from error
    except (OSError, json.JSONDecodeError) as error:
        raise ContractError(f"{label} is not valid JSON: {error}") from error


def schema_path(name: str) -> Path:
    return Path(str(files("deliver_github_issues.schemas").joinpath(f"{name}.schema.json")))


def validate_contract(value: Any, schema_name: str) -> None:
    schema = _read_json(schema_path(schema_name), f"{schema_name} schema")
    errors = sorted(
        Draft202012Validator(schema).iter_errors(value), key=lambda item: list(item.path)
    )
    if errors:
        first = errors[0]
        location = ".".join(str(part) for part in first.absolute_path)
        prefix = f"{location}: " if location else ""
        raise ContractError(prefix + first.message)


def load_queue(path: Path) -> dict[str, Any]:
    queue = _read_json(path, "Queue")
    validate_contract(queue, "queue")
    seen: set[int] = set()
    normalized = deepcopy(queue)
    for issue in normalized["issues"]:
        number = issue["number"]
        if number in seen:
            raise ContractError(f"Duplicate issue number: {number}")
        seen.add(number)
        issue["skills"] = [
            "implement",
            *(skill for skill in issue["skills"] if skill != "implement"),
        ]
    return normalized


def load_policy(path: Path) -> dict[str, Any]:
    policy = _read_json(path, "Repository policy")
    validate_contract(policy, "repository")
    seen: set[str] = set()
    for check in policy["localChecks"]:
        name = check["name"]
        if name in seen:
            raise ContractError(f"Duplicate local check name: {name}")
        seen.add(name)
    return policy
