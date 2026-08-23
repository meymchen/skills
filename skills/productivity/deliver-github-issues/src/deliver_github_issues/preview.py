from __future__ import annotations

from typing import Any


def render_preview(
    queue: dict[str, Any], policy: dict[str, Any], primary_agent: str, metadata_agent: str
) -> str:
    lines = [
        "PREVIEW: validate tools, authentication, repository, clean base branch, labels, and squash setting",
        f"PREVIEW: primary={primary_agent}; metadata={metadata_agent}",
    ]
    for item in queue["issues"]:
        number = item["number"]
        branch = f"{policy['branchPrefix']}{number}"
        skills = ",".join(item["skills"])
        checks = ", ".join(check["name"] for check in policy["localChecks"])
        required = ", ".join(policy["requiredChecks"])
        lines.extend(
            [
                f"#{number}: fetch and fast-forward {queue['baseBranch']}; create {branch}; skills={skills}",
                f"#{number}: invoke implementation agent; run {checks}",
                f"#{number}: generate metadata with {metadata_agent}; commit, push, create PR; wait for {required}",
                f"#{number}: audit checkboxes; update issue evidence; enforce human gate when required",
                f"#{number}: squash merge at tested SHA; delete only {branch}; fast-forward {queue['baseBranch']}",
            ]
        )
    lines.append(
        "PREVIEW: remove the successful run directory; preserve failed state and .scratch/"
    )
    return "\n".join(lines)
