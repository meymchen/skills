# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Structural helper for the verify-acceptance-items skill.

Three subcommands, none of which decide which task-list items are acceptance
items:

* ``links``   resolve the issues a pull request refers to, with provenance.
* ``extract`` report the task-list structure of an issue body.
* ``apply``   tick specific lines of an issue body, byte-exactly.

The model chooses the acceptance section and judges each item. This script only
does the parts that must be deterministic and testable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_PRECONDITION = 2
EXIT_USAGE = 64
EXIT_INTERRUPT = 130

TAB_WIDTH = 4

TASK_ITEM = re.compile(
    r"^(?P<indent>[ \t]*)(?P<marker>[-*+])[ \t]+\[(?P<state>[ xX])\](?P<text>[ \t].*|)$"
)
ATX_HEADING = re.compile(r"^(?P<hashes>#{1,6})[ \t]+(?P<title>.+?)[ \t]*#*[ \t]*$")
BOLD_HEADING = re.compile(r"^(?:\*\*|__)(?P<title>.+?)(?:\*\*|__)[ \t]*[:：]?[ \t]*$")
DETAILS_OPEN = re.compile(r"^[ \t]*<details\b", re.IGNORECASE)
DETAILS_CLOSE = re.compile(r"^[ \t]*</details>", re.IGNORECASE)
SUMMARY_TEXT = re.compile(r"<summary>(?P<title>.*?)</summary>", re.IGNORECASE | re.DOTALL)
FENCE = re.compile(r"^[ \t]*(?P<fence>```+|~~~+)")
TABLE_CHECKBOX = re.compile(r"^[ \t]*\|.*\[[ xX]\]")
SUB_ISSUE = re.compile(
    r"^(?:#\d+"
    r"|[\w.\-]+/[\w.\-]+#\d+"
    r"|https://github\.com/[\w.\-]+/[\w.\-]+/issues/\d+)"
    r"[ \t]*$"
)
BARE_MENTION = re.compile(r"(?<![\w/#])#(?P<number>\d+)\b")
QUALIFIED_MENTION = re.compile(r"\b(?P<repo>[\w.\-]+/[\w.\-]+)#(?P<number>\d+)\b")
BRANCH_NUMBER = re.compile(r"(?<!\d)(?P<number>\d{1,6})(?!\d)")

TRUST = {"closing_reference": "high", "body_mention": "low", "branch_name": "low"}

PERMISSION_HINTS = (
    "403",
    "not authorized",
    "permission",
    "resource not accessible",
    "must have admin",
)


class SkillError(Exception):
    """A failure that should end the run with a specific exit code."""

    def __init__(self, message: str, code: int = EXIT_FAILED) -> None:
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------
# gh access
# --------------------------------------------------------------------------

Runner = Callable[[list[str], str | None], str]


def run_gh(args: list[str], stdin: str | None = None) -> str:
    """Run ``gh`` with an argument list and return stdout."""
    try:
        completed = subprocess.run(
            ["gh", *args],
            input=stdin,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except FileNotFoundError as exc:  # pragma: no cover - environment dependent
        raise SkillError(
            "GitHub CLI (gh) is not installed or not on PATH", EXIT_PRECONDITION
        ) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise SkillError(f"gh {' '.join(args)} failed: {detail}", EXIT_FAILED)
    return completed.stdout


def gh_json(runner: Runner, args: list[str]) -> Any:
    raw = runner(args, None)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SkillError(f"gh {' '.join(args)} returned invalid JSON", EXIT_FAILED) from exc


def default_repo(runner: Runner) -> str:
    data = gh_json(runner, ["repo", "view", "--json", "nameWithOwner"])
    name = data.get("nameWithOwner")
    if not name:
        raise SkillError("could not determine the current repository", EXIT_PRECONDITION)
    return str(name)


def fetch_issue_body(runner: Runner, repo: str, issue: int) -> str:
    data = gh_json(runner, ["issue", "view", str(issue), "--repo", repo, "--json", "body"])
    return data.get("body") or ""


def fetch_issue_comments(runner: Runner, repo: str, issue: int) -> list[dict[str, Any]]:
    data = gh_json(runner, ["issue", "view", str(issue), "--repo", repo, "--json", "comments"])
    comments = data.get("comments") or []
    return [comment for comment in comments if isinstance(comment, dict)]


# --------------------------------------------------------------------------
# Body parsing
# --------------------------------------------------------------------------


@dataclass
class Section:
    kind: str  # heading | bold | details | none
    level: int
    title: str
    line: int
    path: list[str] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)
    emitted: bool = False


def _indent_width(indent: str) -> int:
    width = 0
    for char in indent:
        width = width + TAB_WIDTH - (width % TAB_WIDTH) if char == "\t" else width + 1
    return width


def _path(stack: list[Section]) -> list[str]:
    return [section.title for section in stack]


def parse_body(body: str) -> dict[str, Any]:
    """Report every task-list item in ``body`` without judging any of them.

    Returns sections in document order, each holding the items that appear
    under it, plus the checkboxes deliberately left alone.
    """
    lines = body.split("\n")
    lines = [line[:-1] if line.endswith("\r") else line for line in lines]

    stack: list[Section] = []
    ordered: list[Section] = []
    rootless = Section(kind="none", level=0, title="", line=0)
    skipped: list[dict[str, Any]] = []

    indent_stack: list[int] = []
    recent: dict[int, str] = {}
    last_item: dict[str, Any] | None = None
    last_item_indent = 0
    fence: str | None = None

    def current() -> Section:
        return stack[-1] if stack else rootless

    def close_lists() -> None:
        nonlocal last_item
        indent_stack.clear()
        recent.clear()
        last_item = None

    def open_section(section: Section) -> None:
        close_lists()
        if section.kind == "heading":
            while stack and stack[-1].level >= section.level and stack[-1].kind != "details":
                stack.pop()
        elif section.kind == "bold":
            while stack and stack[-1].kind == "bold":
                stack.pop()
            section.level = (stack[-1].level if stack else 0) + 1
        else:
            section.level = (stack[-1].level if stack else 0) + 1
        stack.append(section)
        section.path = _path(stack)

    for index, line in enumerate(lines, start=1):
        fence_match = FENCE.match(line)
        if fence_match:
            token = fence_match.group("fence")
            if fence is None:
                fence = token[0] * 3
                close_lists()
            elif token[0] == fence[0]:
                fence = None
            continue
        if fence is not None:
            continue

        if DETAILS_CLOSE.match(line):
            close_lists()
            while stack and stack[-1].kind != "details":
                stack.pop()
            if stack:
                stack.pop()
            continue

        if DETAILS_OPEN.match(line):
            summary = SUMMARY_TEXT.search(line)
            title = summary.group("title").strip() if summary else "details"
            open_section(Section(kind="details", level=0, title=title, line=index))
            continue

        heading = ATX_HEADING.match(line)
        if heading:
            open_section(
                Section(
                    kind="heading",
                    level=len(heading.group("hashes")),
                    title=heading.group("title").strip(),
                    line=index,
                )
            )
            continue

        item_match = TASK_ITEM.match(line)
        if item_match:
            width = _indent_width(item_match.group("indent"))
            while indent_stack and indent_stack[-1] >= width:
                indent_stack.pop()
            depth = len(indent_stack)
            indent_stack.append(width)

            text = item_match.group("text").strip()
            item = {
                "id": f"L{index}",
                "line": index,
                "raw": line,
                "text": text,
                "checked": item_match.group("state") != " ",
                "depth": depth,
                "sub_issue": bool(SUB_ISSUE.match(text)),
                "parent": recent.get(depth - 1),
            }
            recent[depth] = item["id"]
            for deeper in [key for key in recent if key > depth]:
                del recent[deeper]

            section = current()
            if not section.emitted:
                section.emitted = True
                ordered.append(section)
            section.items.append(item)
            last_item = item
            last_item_indent = width
            continue

        if TABLE_CHECKBOX.match(line):
            close_lists()
            skipped.append({"reason": "table", "line": index, "raw": line})
            continue

        if not line.strip():
            continue

        bold = BOLD_HEADING.match(line)
        if bold and "**" not in bold.group("title") and "__" not in bold.group("title"):
            open_section(
                Section(kind="bold", level=0, title=bold.group("title").strip(), line=index)
            )
            continue

        if (
            last_item is not None
            and _indent_width(line[: len(line) - len(line.lstrip())]) > last_item_indent
        ):
            last_item["text"] = f"{last_item['text']} {line.strip()}".strip()
            continue

        close_lists()

    sections = [
        {
            "kind": section.kind,
            "title": section.title,
            "heading_line": section.line,
            "heading_path": section.path,
            "items": section.items,
        }
        for section in ordered
    ]
    return {"sections": sections, "skipped": skipped}


# --------------------------------------------------------------------------
# Ticking
# --------------------------------------------------------------------------


def comment_task_lists(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Find task-list items hiding in issue comments.

    These are never acceptance items: a checkbox in a comment is as likely to be
    somebody's scratch list as a requirement. They are reported so the user knows
    the skill saw them and left them alone, rather than silently not looking.
    """
    found: list[dict[str, Any]] = []
    for position, comment in enumerate(comments, start=1):
        parsed = parse_body(comment.get("body") or "")
        for section in parsed["sections"]:
            for item in section["items"]:
                found.append(
                    {
                        "reason": "comment",
                        "comment": position,
                        "comment_url": comment.get("url", ""),
                        "author": (comment.get("author") or {}).get("login", ""),
                        "comment_line": item["line"],
                        "raw": item["raw"],
                        "text": item["text"],
                    }
                )
    return found


def body_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def tick_lines(body: str, requests: list[dict[str, Any]]) -> tuple[str, list[int], list[int]]:
    """Flip ``[ ]`` to ``[x]`` on the requested lines and change nothing else."""
    lines = body.splitlines(keepends=True)
    ticked: list[int] = []
    already: list[int] = []

    for request in sorted(requests, key=lambda item: item["line"]):
        number = request["line"]
        expected = request["raw"]
        if not isinstance(number, int) or number < 1 or number > len(lines):
            raise SkillError(f"line {number} is outside the issue body", EXIT_PRECONDITION)

        original = lines[number - 1]
        stripped = original.rstrip("\r\n")
        ending = original[len(stripped) :]

        if stripped != expected:
            match = TASK_ITEM.match(expected)
            if match and match.group("state") == " ":
                ticked_expected = (
                    expected[: match.start("state")] + "x" + expected[match.start("state") + 1 :]
                )
                if stripped == ticked_expected:
                    already.append(number)
                    continue
            raise SkillError(
                f"line {number} no longer matches the extracted text; re-run extract",
                EXIT_PRECONDITION,
            )

        match = TASK_ITEM.match(stripped)
        if not match:
            raise SkillError(f"line {number} is not a task-list item", EXIT_PRECONDITION)
        if match.group("state") != " ":
            already.append(number)
            continue

        start = match.start("state")
        lines[number - 1] = stripped[:start] + "x" + stripped[start + 1 :] + ending
        ticked.append(number)

    return "".join(lines), ticked, already


# --------------------------------------------------------------------------
# Link resolution
# --------------------------------------------------------------------------


def _repo_from_url(url: str, fallback: str) -> str:
    match = re.search(r"github\.com/([\w.\-]+/[\w.\-]+)/issues/\d+", url or "")
    return match.group(1) if match else fallback


def resolve_links(runner: Runner, repo: str, pr: int) -> dict[str, Any]:
    data = gh_json(
        runner,
        [
            "pr",
            "view",
            str(pr),
            "--repo",
            repo,
            "--json",
            "number,headRefName,body,closingIssuesReferences",
        ],
    )
    body = data.get("body") or ""
    branch = data.get("headRefName") or ""

    found: dict[tuple[str, int], str] = {}

    def note(target_repo: str, number: int, provenance: str) -> None:
        key = (target_repo, number)
        if key not in found:
            found[key] = provenance

    for reference in data.get("closingIssuesReferences") or []:
        number = reference.get("number")
        if isinstance(number, int):
            note(_repo_from_url(reference.get("url", ""), repo), number, "closing_reference")

    for match in QUALIFIED_MENTION.finditer(body):
        note(match.group("repo"), int(match.group("number")), "body_mention")
    for match in BARE_MENTION.finditer(body):
        note(repo, int(match.group("number")), "body_mention")
    for match in BRANCH_NUMBER.finditer(branch):
        note(repo, int(match.group("number")), "branch_name")

    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for (target_repo, number), provenance in found.items():
        if target_repo == repo and number == pr:
            continue
        record = {"repo": target_repo, "number": number, "provenance": provenance}
        try:
            probe = gh_json(runner, ["api", f"repos/{target_repo}/issues/{number}"])
        except SkillError as exc:
            rejected.append({**record, "reason": str(exc)})
            continue
        if probe.get("pull_request") is not None:
            rejected.append({**record, "reason": "number is a pull request, not an issue"})
            continue
        candidates.append(
            {
                **record,
                "trust": TRUST[provenance],
                "title": probe.get("title", ""),
                "state": probe.get("state", ""),
            }
        )

    order = {"closing_reference": 0, "body_mention": 1, "branch_name": 2}
    candidates.sort(key=lambda item: (order[item["provenance"]], item["repo"], item["number"]))
    return {
        "repo": repo,
        "pr": pr,
        "branch": branch,
        "candidates": candidates,
        "rejected": rejected,
    }


# --------------------------------------------------------------------------
# Subcommands
# --------------------------------------------------------------------------


def command_links(args: argparse.Namespace, runner: Runner) -> int:
    repo = args.repo or default_repo(runner)
    print(json.dumps(resolve_links(runner, repo, args.pr), indent=2, ensure_ascii=False))
    return EXIT_OK


def command_extract(args: argparse.Namespace, runner: Runner) -> int:
    repo = args.repo or default_repo(runner)
    body = fetch_issue_body(runner, repo, args.issue)
    parsed = parse_body(body)
    skipped = list(parsed["skipped"])
    if not args.no_comments:
        skipped.extend(comment_task_lists(fetch_issue_comments(runner, repo, args.issue)))
    payload = {
        "repo": repo,
        "issue": args.issue,
        "body_sha256": body_hash(body),
        "sections": parsed["sections"],
        "skipped": skipped,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return EXIT_OK


def command_apply(args: argparse.Namespace, runner: Runner) -> int:
    raw = sys.stdin.read()
    if not raw.strip():
        raise SkillError("apply expects a JSON plan on stdin", EXIT_USAGE)
    try:
        plan = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SkillError(f"the plan on stdin is not valid JSON: {exc}", EXIT_USAGE) from exc

    repo = args.repo or plan.get("repo") or default_repo(runner)
    issue = args.issue or plan.get("issue")
    if not isinstance(issue, int):
        raise SkillError("the plan must name an issue number", EXIT_USAGE)
    expected_hash = plan.get("body_sha256")
    if not expected_hash:
        raise SkillError("the plan must carry the body_sha256 reported by extract", EXIT_USAGE)
    requests = plan.get("tick") or []
    if not isinstance(requests, list):
        raise SkillError("the plan's tick field must be a list", EXIT_USAGE)

    body = fetch_issue_body(runner, repo, issue)
    actual_hash = body_hash(body)
    if actual_hash != expected_hash:
        raise SkillError(
            "the issue body changed since extract ran; re-run extract and rebuild the plan",
            EXIT_PRECONDITION,
        )

    updated, ticked, already = tick_lines(body, requests)

    if ticked and not args.dry_run:
        try:
            runner(["issue", "edit", str(issue), "--repo", repo, "--body-file", "-"], updated)
        except SkillError as exc:
            lowered = str(exc).lower()
            if any(hint in lowered for hint in PERMISSION_HINTS):
                raise SkillError(
                    f"no write access to {repo}; the report above still stands: {exc}",
                    EXIT_PRECONDITION,
                ) from exc
            raise

    payload = {
        "repo": repo,
        "issue": issue,
        "ticked": ticked,
        "already_checked": already,
        "dry_run": bool(args.dry_run),
        "body_sha256_before": actual_hash,
        "body_sha256_after": body_hash(updated),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="verify_acceptance_items.py",
        description="Structural helper for the verify-acceptance-items skill.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    links = subparsers.add_parser("links", help="resolve the issues a pull request refers to")
    links.add_argument("--pr", type=int, required=True)
    links.add_argument("--repo")
    links.set_defaults(handler=command_links)

    extract = subparsers.add_parser("extract", help="report an issue body's task-list structure")
    extract.add_argument("--issue", type=int, required=True)
    extract.add_argument("--repo")
    extract.add_argument(
        "--no-comments",
        action="store_true",
        help="skip the comment scan; body checkboxes are reported either way",
    )
    extract.set_defaults(handler=command_extract)

    apply_parser = subparsers.add_parser("apply", help="tick specific lines of an issue body")
    apply_parser.add_argument("--issue", type=int)
    apply_parser.add_argument("--repo")
    apply_parser.add_argument("--dry-run", action="store_true")
    apply_parser.set_defaults(handler=command_apply)

    return parser


def main(argv: list[str] | None = None, runner: Runner | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args, runner or run_gh)
    except SkillError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        print("interrupted", file=sys.stderr)
        return EXIT_INTERRUPT


if __name__ == "__main__":
    raise SystemExit(main())
