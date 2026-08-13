from __future__ import annotations

import re
from pathlib import Path
from typing import Any


class AuditError(ValueError):
    """Acceptance evidence cannot be verified."""


_CHECKBOX = re.compile(
    r"^(?P<prefix>\s*[-*]\s+)\[(?P<mark>[ xX])\](?P<suffix>\s+)(?P<text>.+?)\s*$",
    re.MULTILINE,
)


def extract_checkboxes(body: str) -> list[dict[str, Any]]:
    return [
        {
            "index": index,
            "text": match.group("text"),
            "checked": match.group("mark").lower() == "x",
        }
        for index, match in enumerate(_CHECKBOX.finditer(body))
    ]


def validate_audit(
    audit: dict[str, Any],
    checkboxes: list[dict[str, Any]],
    root: Path,
    successful_commands: list[str],
    successful_ci_urls: list[str],
) -> None:
    criteria = audit["criteria"]
    if len(criteria) != len(checkboxes):
        raise AuditError("Audit did not return exactly one result per checkbox.")
    resolved_root = root.resolve()
    for index, (criterion, checkbox) in enumerate(zip(criteria, checkboxes, strict=True)):
        if criterion["index"] != index or criterion["text"] != checkbox["text"]:
            raise AuditError(f"Audit checkbox mismatch at index {index}.")
        if criterion["status"] != "satisfied":
            continue
        if not criterion["evidence"]:
            raise AuditError(f"Satisfied checkbox {index} has no evidence.")
        for evidence in criterion["evidence"]:
            kind = evidence["kind"]
            value = evidence["value"]
            valid = False
            if kind == "file":
                relative = re.sub(r":\d+$", "", value)
                candidate = (resolved_root / relative).resolve()
                valid = candidate.is_relative_to(resolved_root) and candidate.is_file()
            elif kind == "command":
                valid = value in successful_commands
            elif kind == "ci":
                valid = value in successful_ci_urls
            if not valid:
                raise AuditError(f"Unverifiable evidence for checkbox {index}: {kind} {value}")


def apply_satisfied_checkboxes(body: str, audit: dict[str, Any]) -> str:
    criteria = iter(audit["criteria"])

    def replace(match: re.Match[str]) -> str:
        criterion = next(criteria)
        if criterion["status"] != "satisfied":
            return match.group(0)
        return f"{match.group('prefix')}[x]{match.group('suffix')}{match.group('text')}"

    return _CHECKBOX.sub(replace, body)
