from __future__ import annotations

import hashlib
import re
from typing import Any

from deliver_github_issues.commands import command_json, run_command


class SelectionError(ValueError):
    """An issue selector is invalid."""


_PART = re.compile(r"^#?(\d+)(?:\s*-\s*#?(\d+))?$")


def parse_issue_selector(selector: str) -> list[int]:
    selected: list[int] = []
    seen: set[int] = set()
    for raw_part in selector.split(","):
        part = raw_part.strip()
        match = _PART.fullmatch(part)
        if not match:
            raise SelectionError(f"Invalid issue selector segment: {part}")
        start = int(match.group(1))
        end = int(match.group(2) or start)
        if start < 1 or end < start:
            raise SelectionError(f"Invalid issue selector range: {part}")
        if end - start + 1 > 500:
            raise SelectionError("Issue selector ranges may contain at most 500 issues")
        for number in range(start, end + 1):
            if number not in seen:
                selected.append(number)
                seen.add(number)
    if not selected:
        raise SelectionError("Issue selector is empty")
    return selected


def resolve_issue_selection(selector: str, ready_label: str) -> dict[str, Any]:
    run_command("gh", ["auth", "status"])
    repository = command_json(
        run_command("gh", ["repo", "view", "--json", "nameWithOwner,defaultBranchRef"]),
        "gh repo view",
    )
    numbers = parse_issue_selector(selector)
    selected = set(numbers)
    order = {number: index for index, number in enumerate(numbers)}
    issues: dict[int, dict[str, Any]] = {}
    for number in numbers:
        issue = command_json(
            run_command(
                "gh",
                [
                    "issue",
                    "view",
                    str(number),
                    "--repo",
                    repository["nameWithOwner"],
                    "--json",
                    "number,title,body,updatedAt,state,labels,blockedBy,blocking",
                ],
            ),
            "gh issue view",
        )
        if issue["state"] != "OPEN":
            raise SelectionError(f"Issue #{number} is not open.")
        if ready_label not in {label["name"] for label in issue["labels"]}:
            raise SelectionError(f"Issue #{number} lacks {ready_label}.")
        for relationship in ("blockedBy", "blocking"):
            relation = issue[relationship]
            if len(relation["nodes"]) != relation["totalCount"]:
                raise SelectionError(
                    f"Issue #{number} has a truncated {relationship} relationship."
                )
        issues[number] = issue

    adjacent = {number: set() for number in numbers}
    indegree = {number: 0 for number in numbers}

    def add_edge(source: int, target: int) -> None:
        if target not in adjacent[source]:
            adjacent[source].add(target)
            indegree[target] += 1

    for number in numbers:
        for dependency in issues[number]["blockedBy"]["nodes"]:
            dependency_number = int(dependency["number"])
            if dependency_number in selected:
                add_edge(dependency_number, number)
            elif dependency["state"] == "OPEN":
                raise SelectionError(
                    f"Issue #{number} is blocked by open issue #{dependency_number}, which is not selected."
                )
        for dependent in issues[number]["blocking"]["nodes"]:
            dependent_number = int(dependent["number"])
            if dependent_number in selected:
                add_edge(number, dependent_number)

    result: list[int] = []
    while len(result) < len(numbers):
        available = [number for number in numbers if number not in result and indegree[number] == 0]
        if not available:
            raise SelectionError("Selected issues contain a dependency cycle.")
        current = min(available, key=order.__getitem__)
        result.append(current)
        for dependent in adjacent[current]:
            indegree[dependent] -= 1
    return {
        "version": 1,
        "repository": repository["nameWithOwner"],
        "baseBranch": repository["defaultBranchRef"]["name"],
        "issues": [
            {
                "number": number,
                "skills": ["implement"],
                "instruction": "",
                "bodyHash": hashlib.sha256(issues[number]["body"].encode("utf-8")).hexdigest(),
            }
            for number in result
        ],
    }


def resolve_all_ready_issues(ready_label: str) -> dict[str, Any]:
    issues = command_json(
        run_command(
            "gh",
            [
                "issue",
                "list",
                "--state",
                "open",
                "--label",
                ready_label,
                "--limit",
                "1000",
                "--json",
                "number",
            ],
        ),
        "gh issue list",
    )
    if not issues:
        raise SelectionError(f"No open issues have the {ready_label} label.")
    selector = ",".join(str(issue["number"]) for issue in issues)
    return resolve_issue_selection(selector, ready_label)
