# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Structural helper for the verify-acceptance-items skill.

Four subcommands, none of which decide which task-list items are acceptance
criteria:

* ``links``   resolve the issues a request refers to, with provenance.
* ``extract`` report the task-list structure of an issue body.
* ``brief``   render the judging subagent's prompt from chosen items.
* ``apply``   tick specific lines of an issue body, byte-exactly.

A request is a GitHub pull request or a GitLab merge request. Every platform
call goes through a forge adapter: ``gh`` for GitHub, ``glab api`` for GitLab.
Parsing, hash checks, ticking, and brief rendering are shared.

The model chooses the acceptance criteria and the subagent judges them. This
script does every part that must be deterministic and testable.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from typing import Any
from urllib.parse import quote

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_PRECONDITION = 2
EXIT_USAGE = 64
EXIT_INTERRUPT = 130

TAB_WIDTH = 4
MAX_LIST_GAP = 4
BRIEF_TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "judge-brief.md"

TASK_ITEM = re.compile(
    r"^(?P<indent>[ \t]*)(?P<marker>[-*+]|\d{1,9}[.)])(?P<gap>[ \t]+)"
    r"\[(?P<state>[ xX~])\](?P<text>[ \t].*|)$"
)
ATX_HEADING = re.compile(r"^(?P<hashes>#{1,6})[ \t]+(?P<title>.+?)[ \t]*(?:#+[ \t]*)?$")
BOLD_HEADING = re.compile(r"^(?:\*\*|__)(?P<title>.+?)(?:\*\*|__)[ \t]*(?:[:：][ \t]*)?$")
DETAILS_OPEN = re.compile(r"^[ \t]*<details\b", re.IGNORECASE)
DETAILS_CLOSE = re.compile(r"^[ \t]*</details>", re.IGNORECASE)
SUMMARY_TEXT = re.compile(r"<summary>(?P<title>.*?)</summary>", re.IGNORECASE | re.DOTALL)
FENCE = re.compile(r"^[ \t]*(?P<fence>```+|~~~+)")
TABLE_CHECKBOX = re.compile(r"^[ \t]*\|.*\[[ xX~]\]")
PROJECT_PATH = re.compile(r"^[\w.\-]+(?:/[\w.\-]+)+$")
REFERENCE_ONLY = re.compile(r"^(?:[\w.\-]+(?:/[\w.\-]+)*)?#\d+(?:\+s?)?$")
WEB_URL = re.compile(r"https?://(?P<host>[\w.\-]+(?::\d+)?)/(?P<rest>[\w.\-/+]+)")
URL_NUMBER = re.compile(r"^(?P<number>\d+)(?:\+s?)?$")
BARE_MENTION = re.compile(r"(?<![\w/#])#(?P<number>\d+)\b")
QUALIFIED_MENTION = re.compile(r"(?<![\w./\-])(?P<repo>[\w.\-]+(?:/[\w.\-]+)+)#(?P<number>\d+)\b")
BRANCH_NUMBER = re.compile(r"(?<!\d)(?P<number>\d{1,6})(?!\d)")
LINE_ID = re.compile(r"^L?(?P<line>[1-9]\d*)$")
ITEM_SPEC = re.compile(
    r"^(?:(?P<repo>[\w.\-]+(?:/[\w.\-]+)+))?#(?P<issue>\d+):(?P<lines>L?\d+(?:,L?\d+)*)$"
)
SCHEME_REMOTE = re.compile(
    r"^(?P<scheme>[a-z][a-z0-9+.\-]*)://(?:[^@/]+@)?(?P<host>[^/:]+)(?::(?P<port>\d+))?/(?P<path>.+)$",
    re.IGNORECASE,
)
SCP_REMOTE = re.compile(r"^(?:[^@/]+@)?(?P<host>[^/:]+):(?P<path>[^/].*)$")

ISSUE_KINDS = ("issues", "work_items")
TRUST = {
    "closing_reference": "high",
    "body_mention": "low",
    "related_issue": "low",
    "branch_name": "low",
}
PROVENANCE_ORDER = {"closing_reference": 0, "body_mention": 1, "related_issue": 1, "branch_name": 2}

PERMISSION_HINTS = (
    "401",
    "403",
    "forbidden",
    "unauthorized",
    "not authorized",
    "insufficient_scope",
    "insufficient scope",
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
# Command access
# --------------------------------------------------------------------------

Runner = Callable[[list[str], str | None], str]


def run_command(args: list[str], stdin: str | None = None) -> str:
    """Run ``git``, ``gh``, or ``glab`` with an argument list and return stdout.

    The pipes run in binary mode: text mode on Windows rewrites every ``\\n``
    written to stdin as ``\\r\\n``, which would corrupt an issue body on write.
    """
    try:
        completed = subprocess.run(
            args,
            input=None if stdin is None else stdin.encode("utf-8"),
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as exc:  # pragma: no cover - environment dependent
        raise SkillError(f"{args[0]} is not installed or not on PATH", EXIT_PRECONDITION) from exc
    stdout = completed.stdout.decode("utf-8")
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace")
        detail = (stderr or stdout).strip()
        raise SkillError(f"{' '.join(args)} failed: {detail}", EXIT_FAILED)
    return stdout


def load_json(raw: str, args: list[str]) -> Any:
    """Decode a JSON document, or the concatenated arrays of a paginated read."""
    decoder = json.JSONDecoder()
    values: list[Any] = []
    position = 0
    text = raw.strip()
    try:
        while position < len(text):
            value, end = decoder.raw_decode(text, position)
            values.append(value)
            position = end
            while position < len(text) and text[position].isspace():
                position += 1
    except json.JSONDecodeError as exc:
        raise SkillError(f"{' '.join(args)} returned invalid JSON", EXIT_FAILED) from exc
    if not values:
        raise SkillError(f"{' '.join(args)} returned no JSON", EXIT_FAILED)
    if len(values) == 1:
        return values[0]
    if all(isinstance(value, list) for value in values):
        return [entry for value in values for entry in value]
    raise SkillError(f"{' '.join(args)} returned several JSON documents", EXIT_FAILED)


def run_json(runner: Runner, args: list[str]) -> Any:
    return load_json(runner(args, None), args)


def is_permission_failure(exc: SkillError) -> bool:
    lowered = str(exc).lower()
    return any(hint in lowered for hint in PERMISSION_HINTS)


# --------------------------------------------------------------------------
# Platform detection
# --------------------------------------------------------------------------

GITHUB_HOST = "github.com"
GITLAB_HOST = "gitlab.com"
CLI = {"github": "gh", "gitlab": "glab"}


@dataclass
class Remote:
    host: str
    path: str
    branch: str | None


def parse_remote_url(url: str) -> tuple[str, str] | None:
    """Split a clone or web URL into its host and project path."""
    url = url.strip()
    match = SCHEME_REMOTE.match(url)
    if match:
        host = match.group("host").lower()
        if match.group("port") and match.group("scheme").lower() in ("http", "https"):
            host = f"{host}:{match.group('port')}"
        path = match.group("path")
    else:
        match = SCP_REMOTE.match(url)
        if not match:
            return None
        host = match.group("host").lower()
        path = match.group("path")
    path = path.split("/-/", 1)[0].strip("/").removesuffix(".git")
    if not PROJECT_PATH.match(path):
        return None
    return host, path


def split_repo_arg(value: str) -> tuple[str | None, str]:
    """Accept ``group/sub/project`` or a project URL for ``--repo``."""
    value = value.strip()
    if "://" in value or "@" in value:
        parsed = parse_remote_url(value)
        if parsed is None:
            raise SkillError(f"{value!r} is not a project URL", EXIT_USAGE)
        return parsed
    value = value.strip("/")
    if not PROJECT_PATH.match(value):
        raise SkillError(
            f"{value!r} is not a project path such as owner/name or group/sub/project", EXIT_USAGE
        )
    return None, value


def read_remote(runner: Runner) -> Remote:
    """The current branch's upstream remote, or ``origin`` when it has none."""
    try:
        branch = runner(["git", "rev-parse", "--abbrev-ref", "HEAD"], None).strip()
    except SkillError as exc:
        raise SkillError(
            f"not in a git repository; pass --host and --repo: {exc}", EXIT_PRECONDITION
        ) from exc

    def config(key: str) -> str | None:
        try:
            return runner(["git", "config", "--get", key], None).strip() or None
        except SkillError:
            return None

    remote = upstream = None
    if branch and branch != "HEAD":
        remote = config(f"branch.{branch}.remote")
        upstream = config(f"branch.{branch}.merge")
    if not remote or remote == ".":
        remote, upstream = "origin", None
    try:
        url = runner(["git", "remote", "get-url", remote], None).strip()
    except SkillError as exc:
        raise SkillError(
            f"the branch has no remote {remote!r}; pass --host and --repo", EXIT_PRECONDITION
        ) from exc
    parsed = parse_remote_url(url)
    if parsed is None:
        raise SkillError(
            f"cannot read a host and project from remote URL {url!r}", EXIT_PRECONDITION
        )
    head = upstream.removeprefix("refs/heads/") if upstream else None
    return Remote(
        host=parsed[0], path=parsed[1], branch=head or (branch if branch != "HEAD" else None)
    )


def cli_authenticated(runner: Runner, cli: str, host: str) -> bool:
    try:
        runner([cli, "auth", "status", "--hostname", host], None)
    except SkillError:
        return False
    return True


def detect_platform(runner: Runner, host: str) -> str:
    """Apply the git-workflows rules to ``host``."""
    name = host.lower().split(":", 1)[0]
    if name == GITHUB_HOST:
        return "github"
    if name == GITLAB_HOST or "gitlab" in name:
        return "gitlab"
    github = cli_authenticated(runner, "gh", host)
    gitlab = cli_authenticated(runner, "glab", host)
    if github != gitlab:
        return "github" if github else "gitlab"
    which = "both gh and glab are" if github else "neither gh nor glab is"
    raise SkillError(
        f"cannot tell whether {host} is GitHub or GitLab: {which} authenticated for it; "
        "ask the user which it is, then pass --platform github or --platform gitlab",
        EXIT_PRECONDITION,
    )


def parse_item_url(url: str) -> tuple[str, str, str, int] | None:
    """Split an issue, work item, pull, or merge request URL into its parts."""
    match = WEB_URL.fullmatch(url.strip())
    if not match:
        return None
    parts = match.group("rest").rstrip("/").split("/")
    if len(parts) < 4:
        return None
    number = URL_NUMBER.match(parts[-1])
    if not number:
        return None
    kind = parts[-2]
    path_parts = parts[:-2]
    if path_parts and path_parts[-1] == "-":
        path_parts = path_parts[:-1]
    path = "/".join(path_parts)
    if not PROJECT_PATH.match(path):
        return None
    return match.group("host").lower(), path, kind, int(number.group("number"))


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


def _indent_width(indent: str, start: int = 0) -> int:
    width = start
    for char in indent:
        width = width + TAB_WIDTH - (width % TAB_WIDTH) if char == "\t" else width + 1
    return width - start


def _path(stack: list[Section]) -> list[str]:
    return [section.title for section in stack]


def _content_column(match: re.Match[str]) -> tuple[int, int]:
    """Return the item's indent and the column its content starts at.

    The content column follows the marker's actual width, so ``10. `` takes four
    columns, and a child item must be indented at least that far.
    """
    indent = _indent_width(match.group("indent"))
    marker_end = indent + len(match.group("marker"))
    gap = _indent_width(match.group("gap"), marker_end)
    return indent, marker_end + (gap if gap <= MAX_LIST_GAP else 1)


def is_reference_only(text: str, host: str | None) -> bool:
    """True for an item that only names another issue, which is never ticked."""
    if REFERENCE_ONLY.match(text):
        return True
    if host is None:
        return False
    parsed = parse_item_url(text)
    return parsed is not None and parsed[0] == host.lower() and parsed[2] in ISSUE_KINDS


def item_state(mark: str) -> str:
    if mark == " ":
        return "unchecked"
    if mark == "~":
        return "inapplicable"
    return "checked"


def parse_body(body: str, host: str | None = None) -> dict[str, Any]:
    """Report every task-list item in ``body`` without judging any of them.

    Returns sections in document order, each holding the items that appear
    under it, plus the checkboxes deliberately left alone. ``host`` is the
    detected host, whose issue URLs count as reference-only items.
    """
    lines = body.split("\n")
    lines = [line[:-1] if line.endswith("\r") else line for line in lines]

    stack: list[Section] = []
    ordered: list[Section] = []
    rootless = Section(kind="none", level=0, title="", line=0)
    skipped: list[dict[str, Any]] = []

    open_items: list[tuple[int, str]] = []  # (content column, item id) per depth
    last_item: dict[str, Any] | None = None
    last_item_indent = 0
    fence: str | None = None

    def current() -> Section:
        return stack[-1] if stack else rootless

    def close_lists() -> None:
        nonlocal last_item
        open_items.clear()
        last_item = None

    def open_section(section: Section) -> None:
        close_lists()
        # A bold line is the weakest boundary: whatever section opens next ends it.
        while stack and stack[-1].kind == "bold":
            stack.pop()
        if section.kind == "heading":
            while stack and stack[-1].level >= section.level and stack[-1].kind != "details":
                stack.pop()
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
            width, content = _content_column(item_match)
            while open_items and width < open_items[-1][0]:
                open_items.pop()
            depth = len(open_items)

            text = item_match.group("text").strip()
            item = {
                "id": f"L{index}",
                "line": index,
                "raw": line,
                "text": text,
                "state": item_state(item_match.group("state")),
                "depth": depth,
                "sub_issue": is_reference_only(text, host),
                "parent": open_items[-1][1] if open_items else None,
            }
            open_items.append((content, item["id"]))

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


def comment_task_lists(
    comments: list[dict[str, Any]], host: str | None = None
) -> list[dict[str, Any]]:
    """Find task-list items hiding in issue comments.

    These are never acceptance items: a checkbox in a comment is as likely to be
    somebody's scratch list as a requirement. They are reported so the user knows
    the skill saw them and left them alone, rather than silently not looking.
    Each comment is ``{"body", "author", "url"}`` as the forge adapter returns it.
    """
    found: list[dict[str, Any]] = []
    for position, comment in enumerate(comments, start=1):
        parsed = parse_body(comment.get("body") or "", host)
        for section in parsed["sections"]:
            for item in section["items"]:
                found.append(
                    {
                        "reason": "comment",
                        "comment": position,
                        "comment_url": comment.get("url") or "",
                        "author": comment.get("author") or "",
                        "comment_line": item["line"],
                        "raw": item["raw"],
                        "text": item["text"],
                    }
                )
    return found


def body_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def body_items(body: str, host: str | None = None) -> dict[int, dict[str, Any]]:
    """Every task-list item ``parse_body`` reports, keyed by line number."""
    parsed = parse_body(body, host)
    return {item["line"]: item for section in parsed["sections"] for item in section["items"]}


def tickable_item(items: dict[int, dict[str, Any]], number: int) -> dict[str, Any]:
    """Return the item on ``number``, refusing anything that must never be ticked."""
    item = items.get(number)
    if item is None:
        raise SkillError(
            f"L{number} is not a task-list item in the issue body "
            "(prose, a table, fenced code, or outside the body)",
            EXIT_USAGE,
        )
    if item["sub_issue"]:
        raise SkillError(
            f"L{number} only references another issue, which is never ticked", EXIT_USAGE
        )
    if item["state"] == "inapplicable":
        raise SkillError(
            f"L{number} is marked inapplicable ([~]), which is never ticked", EXIT_USAGE
        )
    return item


def tick_lines(
    body: str, targets: list[int], host: str | None = None
) -> tuple[str, list[int], list[int]]:
    """Flip ``[ ]`` to ``[x]`` on the requested lines and change nothing else.

    Lines are split on ``\n`` exactly as ``parse_body`` splits them, so the line
    numbers ``extract`` reported address the same lines here.
    """
    items = body_items(body, host)
    lines = body.split("\n")
    ticked: list[int] = []
    already: list[int] = []

    for number in sorted(set(targets)):
        item = tickable_item(items, number)
        if item["state"] == "checked":
            already.append(number)
            continue
        original = lines[number - 1]
        match = TASK_ITEM.match(original.removesuffix("\r"))
        if not match:  # pragma: no cover - parse_body already matched this line
            raise SkillError(f"L{number} is not a task-list item", EXIT_USAGE)
        start = match.start("state")
        lines[number - 1] = original[:start] + "x" + original[start + 1 :]
        ticked.append(number)

    return "\n".join(lines), ticked, already


def check_read_back(sent: str, stored: str) -> None:
    """Fail when the stored body is not the text sent, line endings aside."""
    expected = sent.replace("\r\n", "\n")
    actual = stored.replace("\r\n", "\n")
    if expected == actual:
        return
    diff = list(
        difflib.unified_diff(
            expected.split("\n"), actual.split("\n"), "sent", "stored", lineterm="", n=0
        )
    )
    shown = diff[:20]
    if len(diff) > len(shown):
        shown.append(f"... {len(diff) - len(shown)} more diff lines")
    raise SkillError(
        "the write went through, but the body read back differs from the text sent:\n"
        + "\n".join(shown),
        EXIT_FAILED,
    )


def line_id(value: str) -> int:
    """Parse an item id as ``extract`` reports it (``L13``), or a bare line number."""
    match = LINE_ID.match(value.strip())
    if not match:
        raise argparse.ArgumentTypeError(f"{value!r} is not an item id such as L13")
    return int(match.group("line"))


# --------------------------------------------------------------------------
# Forge adapters
# --------------------------------------------------------------------------


@dataclass
class Ref:
    host: str
    repo: str
    number: int
    provenance: str


@dataclass
class Request:
    repo: str
    number: int
    branch: str
    refs: list[Ref]
    rejected: list[dict[str, Any]] = field(default_factory=list)


class Forge:
    """Every platform call the skill makes. Subclasses implement one platform."""

    platform = ""
    cli = ""
    request_noun = ""
    request_term = ""
    request_sigil = ""
    check_noun = ""
    # GitHub numbers issues and pull requests in one sequence; GitLab does not.
    shared_numbers = False

    def __init__(self, runner: Runner, host: str) -> None:
        self.runner = runner
        self.host = host

    def check_auth(self) -> None:
        if not cli_authenticated(self.runner, self.cli, self.host):
            raise SkillError(
                f"{self.cli} is not installed or not authenticated for {self.host}; "
                f"run `{self.cli} auth login --hostname {self.host}`",
                EXIT_PRECONDITION,
            )

    def label(self, number: int) -> str:
        return f"{self.request_term} {self.request_sigil}{number}"

    def resolve_current(self, session: Session) -> Request:
        raise NotImplementedError

    def resolve(self, repo: str, number: int) -> Request:
        raise NotImplementedError

    def issue_info(self, repo: str, number: int) -> dict[str, Any]:
        raise NotImplementedError

    def issue_body(self, repo: str, number: int) -> str:
        raise NotImplementedError

    def issue_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        raise NotImplementedError

    def write_issue_body(self, repo: str, number: int, body: str) -> None:
        raise NotImplementedError

    def evidence(self, repo: str, number: int) -> str:
        raise NotImplementedError


class GitHubForge(Forge):
    platform = "github"
    cli = "gh"
    request_noun = "pull request"
    request_term = "PR"
    request_sigil = "#"
    check_noun = "check name"
    shared_numbers = True

    def _repo(self, repo: str) -> str:
        return f"{self.host}/{repo}"

    def _json(self, args: list[str]) -> Any:
        return run_json(self.runner, ["gh", *args])

    def _request(self, target: list[str], fallback_repo: str | None) -> Request:
        data = self._json(
            ["pr", "view", *target, "--json", "number,url,headRefName,body,closingIssuesReferences"]
        )
        number = data.get("number")
        if not isinstance(number, int):
            raise SkillError("could not determine the pull request", EXIT_PRECONDITION)
        parsed = parse_item_url(data.get("url") or "")
        repo = parsed[1] if parsed else fallback_repo
        if repo is None:
            raise SkillError("could not determine the pull request's repository", EXIT_PRECONDITION)
        body = data.get("body") or ""
        refs: list[Ref] = []
        for reference in data.get("closingIssuesReferences") or []:
            issue = reference.get("number")
            if not isinstance(issue, int):
                continue
            url = parse_item_url(reference.get("url") or "")
            host, issue_repo = (url[0], url[1]) if url else (self.host, repo)
            refs.append(Ref(host, issue_repo, issue, "closing_reference"))
        for match in WEB_URL.finditer(body):
            url = parse_item_url(match.group(0))
            if url and url[2] in ISSUE_KINDS:
                refs.append(Ref(url[0], url[1], url[3], "body_mention"))
        for match in QUALIFIED_MENTION.finditer(body):
            refs.append(
                Ref(self.host, match.group("repo"), int(match.group("number")), "body_mention")
            )
        for match in BARE_MENTION.finditer(body):
            refs.append(Ref(self.host, repo, int(match.group("number")), "body_mention"))
        return Request(repo=repo, number=number, branch=data.get("headRefName") or "", refs=refs)

    def resolve_current(self, session: Session) -> Request:
        return self._request([], None)

    def resolve(self, repo: str, number: int) -> Request:
        return self._request([str(number), "--repo", self._repo(repo)], repo)

    def issue_info(self, repo: str, number: int) -> dict[str, Any]:
        probe = self._json(["api", "--hostname", self.host, f"repos/{repo}/issues/{number}"])
        if probe.get("pull_request") is not None:
            raise SkillError("number is a pull request, not an issue")
        return {"title": probe.get("title", ""), "state": probe.get("state", "")}

    def issue_body(self, repo: str, number: int) -> str:
        data = self._json(
            ["issue", "view", str(number), "--repo", self._repo(repo), "--json", "body"]
        )
        return data.get("body") or ""

    def issue_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        data = self._json(
            ["issue", "view", str(number), "--repo", self._repo(repo), "--json", "comments"]
        )
        return [
            {
                "body": comment.get("body") or "",
                "author": (comment.get("author") or {}).get("login", ""),
                "url": comment.get("url", ""),
            }
            for comment in data.get("comments") or []
            if isinstance(comment, dict)
        ]

    def write_issue_body(self, repo: str, number: int, body: str) -> None:
        self.runner(
            ["gh", "issue", "edit", str(number), "--repo", self._repo(repo), "--body-file", "-"],
            body,
        )

    def evidence(self, repo: str, number: int) -> str:
        target = f"{number} --repo {self._repo(repo)}"
        return "\n".join(
            [
                f"- `gh pr diff {target}`",
                f"- `gh pr checks {target}`",
                f"- `gh pr view {target} --json title,body,commits`",
            ]
        )


class GitLabForge(Forge):
    platform = "gitlab"
    cli = "glab"
    request_noun = "merge request"
    request_term = "MR"
    request_sigil = "!"
    check_noun = "job name"

    @staticmethod
    def _project(repo: str) -> str:
        return quote(repo, safe="")

    def _api(self, endpoint: str, *extra: str, paginate: bool = False) -> Any:
        args = ["glab", "api", "--hostname", self.host]
        if paginate:
            args.append("--paginate")
        return run_json(self.runner, [*args, endpoint, *extra])

    def _issue_refs(self, issues: Any, provenance: str, request: Request) -> None:
        for issue in issues if isinstance(issues, list) else []:
            if not isinstance(issue, dict):
                continue
            url = parse_item_url(issue.get("web_url") or "")
            if url is None or url[2] not in ISSUE_KINDS:
                request.rejected.append(
                    {
                        "host": self.host,
                        "repo": "",
                        "number": issue.get("iid") or issue.get("id"),
                        "provenance": provenance,
                        "reason": f"not an issue on {self.host}: {issue.get('title', '')}".strip(),
                    }
                )
                continue
            request.refs.append(Ref(url[0], url[1], url[3], provenance))

    def resolve_current(self, session: Session) -> Request:
        repo = session.default_repo()
        branch = session.remote().branch
        if not branch:
            raise SkillError("the current branch is detached; pass --request", EXIT_PRECONDITION)
        found = self._api(
            f"projects/{self._project(repo)}/merge_requests",
            "-X",
            "GET",
            "-f",
            f"source_branch={branch}",
            "-f",
            "state=opened",
            paginate=True,
        )
        iids = sorted(
            entry["iid"]
            for entry in found
            if isinstance(entry, dict) and isinstance(entry.get("iid"), int)
        )
        if not iids:
            raise SkillError(
                f"no open merge request in {repo} has source branch {branch!r}; pass --request",
                EXIT_PRECONDITION,
            )
        if len(iids) > 1:
            listed = ", ".join(f"!{iid}" for iid in iids)
            raise SkillError(
                f"several open merge requests have source branch {branch!r} ({listed}); "
                "pass --request",
                EXIT_PRECONDITION,
            )
        return self.resolve(repo, iids[0])

    def resolve(self, repo: str, number: int) -> Request:
        base = f"projects/{self._project(repo)}/merge_requests/{number}"
        data = self._api(base)
        request = Request(repo=repo, number=number, branch=data.get("source_branch") or "", refs=[])
        self._issue_refs(
            self._api(f"{base}/closes_issues", paginate=True), "closing_reference", request
        )
        self._issue_refs(
            self._api(f"{base}/related_issues", paginate=True), "related_issue", request
        )
        return request

    def _issue(self, repo: str, number: int) -> dict[str, Any]:
        data = self._api(f"projects/{self._project(repo)}/issues/{number}")
        if not isinstance(data, dict):
            raise SkillError(f"{repo}#{number} did not return an issue")
        return data

    def issue_info(self, repo: str, number: int) -> dict[str, Any]:
        data = self._issue(repo, number)
        return {"title": data.get("title", ""), "state": data.get("state", "")}

    def issue_body(self, repo: str, number: int) -> str:
        return self._issue(repo, number).get("description") or ""

    def issue_comments(self, repo: str, number: int) -> list[dict[str, Any]]:
        notes = self._api(
            f"projects/{self._project(repo)}/issues/{number}/notes",
            "-X",
            "GET",
            "-f",
            "sort=asc",
            "-f",
            "order_by=created_at",
            "-f",
            "per_page=100",
            paginate=True,
        )
        return [
            {
                "body": note.get("body") or "",
                "author": (note.get("author") or {}).get("username", ""),
                "url": f"https://{self.host}/{repo}/-/issues/{number}#note_{note.get('id')}",
            }
            for note in notes
            if isinstance(note, dict) and not note.get("system")
        ]

    def write_issue_body(self, repo: str, number: int, body: str) -> None:
        # The description travels on stdin: an argument is length-limited and
        # visible to every process on the machine.
        self.runner(
            [
                "glab",
                "api",
                "--hostname",
                self.host,
                "-X",
                "PUT",
                f"projects/{self._project(repo)}/issues/{number}",
                "-H",
                "Content-Type: application/json",
                "--input",
                "-",
            ],
            json.dumps({"description": body}, ensure_ascii=False),
        )

    def evidence(self, repo: str, number: int) -> str:
        api = f"glab api --hostname {self.host} projects/{self._project(repo)}"
        return "\n".join(
            [
                f"- `glab mr diff {number} --raw --repo https://{self.host}/{repo}`",
                f"- `{api}/merge_requests/{number}` for the title, the description, and"
                " `head_pipeline`",
                f"- `{api}/merge_requests/{number}/commits`",
                f"- `{api}/pipelines/<id>/jobs`, with the `id` of `head_pipeline` above, for"
                " the jobs of the head pipeline",
            ]
        )


FORGES: dict[str, type[Forge]] = {"github": GitHubForge, "gitlab": GitLabForge}


class Session:
    """The host, platform, and adapter one command runs against."""

    def __init__(self, args: argparse.Namespace, runner: Runner) -> None:
        self.runner = runner
        self._remote: Remote | None = None
        repo_host, self.repo = split_repo_arg(args.repo) if args.repo else (None, None)
        self.host = (args.host or repo_host or self.remote().host).lower()
        platform = args.platform or detect_platform(runner, self.host)
        self.forge = FORGES[platform](runner, self.host)
        self.forge.check_auth()

    def remote(self) -> Remote:
        if self._remote is None:
            self._remote = read_remote(self.runner)
        return self._remote

    def default_repo(self) -> str:
        return self.repo or self.remote().path

    def header(self) -> dict[str, Any]:
        return {"host": self.host, "platform": self.forge.platform}


# --------------------------------------------------------------------------
# Judging brief
# --------------------------------------------------------------------------


def parse_item_spec(spec: str, default: str) -> tuple[str, int, list[int]]:
    match = ITEM_SPEC.match(spec.strip())
    if not match:
        raise SkillError(
            f"{spec!r} is not an item selection such as group/project#42:L13,L15", EXIT_USAGE
        )
    try:
        lines = [line_id(value) for value in match.group("lines").split(",")]
    except argparse.ArgumentTypeError as exc:
        raise SkillError(f"{spec!r}: {exc}", EXIT_USAGE) from exc
    return match.group("repo") or default, int(match.group("issue")), lines


def render_brief(
    forge: Forge, repo: str, request: int, groups: list[tuple[str, int, list[dict[str, Any]]]]
) -> str:
    """Fill the judging template with the chosen items and nothing else.

    Item state is left out on purpose: an existing tick is not evidence.
    """
    blocks: list[str] = []
    for issue_repo, issue, items in groups:
        base = min(item["depth"] for item in items)
        rows = [f"{'  ' * (item['depth'] - base)}- {item['id']}: {item['text']}" for item in items]
        blocks.append(f"### {issue_repo}#{issue}\n\n" + "\n".join(rows))
    template = Template(BRIEF_TEMPLATE.read_text(encoding="utf-8"))
    return template.substitute(
        label=forge.label(request),
        request=f"{forge.request_noun} {forge.request_sigil}{request} in `{repo}` on `{forge.host}`",
        noun=forge.request_noun,
        evidence=forge.evidence(repo, request),
        check=forge.check_noun,
        criteria="\n\n".join(blocks),
    )


# --------------------------------------------------------------------------
# Link resolution
# --------------------------------------------------------------------------


def resolve_links(session: Session, request_number: int | None) -> dict[str, Any]:
    """Resolve the issues a request refers to; ``None`` means the current branch's."""
    forge = session.forge
    if request_number is None:
        request = forge.resolve_current(session)
    else:
        request = forge.resolve(session.default_repo(), request_number)

    for match in BRANCH_NUMBER.finditer(request.branch):
        request.refs.append(
            Ref(forge.host, request.repo, int(match.group("number")), "branch_name")
        )

    found: dict[tuple[str, str, int], str] = {}
    for ref in request.refs:
        found.setdefault((ref.host.lower(), ref.repo, ref.number), ref.provenance)

    candidates: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = list(request.rejected)
    for (host, repo, number), provenance in found.items():
        if (
            forge.shared_numbers
            and host == forge.host
            and repo == request.repo
            and number == request.number
        ):
            continue
        record = {"host": host, "repo": repo, "number": number, "provenance": provenance}
        if host != forge.host:
            rejected.append({**record, "reason": f"issue is on {host}, not {forge.host}"})
            continue
        try:
            info = forge.issue_info(repo, number)
        except SkillError as exc:
            rejected.append({**record, "reason": str(exc)})
            continue
        candidates.append({**record, "trust": TRUST[provenance], **info})

    candidates.sort(
        key=lambda item: (PROVENANCE_ORDER[item["provenance"]], item["repo"], item["number"])
    )
    return {
        **session.header(),
        "repo": request.repo,
        "request": request.number,
        "request_label": forge.label(request.number),
        "branch": request.branch,
        "candidates": candidates,
        "rejected": rejected,
    }


# --------------------------------------------------------------------------
# Subcommands
# --------------------------------------------------------------------------


def command_links(args: argparse.Namespace, runner: Runner) -> int:
    if args.request is None and args.repo:
        raise SkillError(
            "--repo needs --request; the current branch only names its own request", EXIT_USAGE
        )
    session = Session(args, runner)
    print(json.dumps(resolve_links(session, args.request), indent=2, ensure_ascii=False))
    return EXIT_OK


def command_extract(args: argparse.Namespace, runner: Runner) -> int:
    session = Session(args, runner)
    forge = session.forge
    repo = session.default_repo()
    body = forge.issue_body(repo, args.issue)
    parsed = parse_body(body, session.host)
    skipped = list(parsed["skipped"])
    if not args.no_comments:
        skipped.extend(comment_task_lists(forge.issue_comments(repo, args.issue), session.host))
    payload = {
        **session.header(),
        "repo": repo,
        "issue": args.issue,
        "body_sha256": body_hash(body),
        "sections": parsed["sections"],
        "skipped": skipped,
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return EXIT_OK


def command_brief(args: argparse.Namespace, runner: Runner) -> int:
    specs = [parse_item_spec(spec, "") for spec in args.items]  # usage errors before any call
    session = Session(args, runner)
    repo = session.default_repo()
    groups: list[tuple[str, int, list[dict[str, Any]]]] = []
    for issue_repo, issue, numbers in specs:
        issue_repo = issue_repo or repo
        items = body_items(session.forge.issue_body(issue_repo, issue), session.host)
        chosen = [tickable_item(items, number) for number in sorted(set(numbers))]
        groups.append((issue_repo, issue, chosen))
    sys.stdout.write(render_brief(session.forge, repo, args.request, groups))
    return EXIT_OK


def command_apply(args: argparse.Namespace, runner: Runner) -> int:
    session = Session(args, runner)
    forge = session.forge
    repo = session.default_repo()
    issue = args.issue

    # A matching hash means the body is the one extract numbered, so line
    # numbers alone address the right lines.
    body = forge.issue_body(repo, issue)
    actual_hash = body_hash(body)
    if actual_hash != args.body_sha256:
        raise SkillError(
            "the issue body changed since extract ran; re-run extract and choose again",
            EXIT_PRECONDITION,
        )

    updated, ticked, already = tick_lines(body, args.tick, session.host)

    read_back = None
    if ticked and not args.dry_run:
        try:
            forge.write_issue_body(repo, issue, updated)
        except SkillError as exc:
            if is_permission_failure(exc):
                raise SkillError(
                    f"no write access to {repo} on {session.host}; the report above still "
                    f"stands: {exc}",
                    EXIT_PRECONDITION,
                ) from exc
            raise
        check_read_back(updated, forge.issue_body(repo, issue))
        read_back = "matched"

    payload = {
        **session.header(),
        "repo": repo,
        "issue": issue,
        "ticked": ticked,
        "already_checked": already,
        "dry_run": bool(args.dry_run),
        "read_back": read_back,
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
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--repo",
        metavar="PATH_OR_URL",
        help="project path such as group/sub/project, or a project URL",
    )
    common.add_argument("--host", help="overrides the host detected from the git remote")
    common.add_argument(
        "--platform", choices=sorted(FORGES), help="overrides the detected platform"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    links = subparsers.add_parser(
        "links", parents=[common], help="resolve the issues a request refers to"
    )
    links.add_argument("--request", type=int, help="defaults to the current branch's request")
    links.set_defaults(handler=command_links)

    extract = subparsers.add_parser(
        "extract", parents=[common], help="report an issue body's task-list structure"
    )
    extract.add_argument("--issue", type=int, required=True)
    extract.add_argument(
        "--no-comments",
        action="store_true",
        help="skip the comment scan; body checkboxes are reported either way",
    )
    extract.set_defaults(handler=command_extract)

    brief = subparsers.add_parser(
        "brief", parents=[common], help="render the judging subagent's prompt"
    )
    brief.add_argument("--request", type=int, required=True)
    brief.add_argument(
        "--items",
        nargs="+",
        required=True,
        metavar="[PROJECT]#ISSUE:Ln,Ln",
        help="chosen acceptance criteria, one selection per issue; PROJECT defaults to --repo",
    )
    brief.set_defaults(handler=command_brief)

    apply_parser = subparsers.add_parser(
        "apply", parents=[common], help="tick specific lines of an issue body"
    )
    apply_parser.add_argument("--issue", type=int, required=True)
    apply_parser.add_argument(
        "--body-sha256", required=True, help="the body_sha256 that extract reported"
    )
    apply_parser.add_argument(
        "--tick", nargs="+", type=line_id, required=True, metavar="Ln", help="item ids to tick"
    )
    apply_parser.add_argument("--dry-run", action="store_true")
    apply_parser.set_defaults(handler=command_apply)

    return parser


def main(argv: list[str] | None = None, runner: Runner | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.handler(args, runner or run_command)
    except SkillError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        print("interrupted", file=sys.stderr)
        return EXIT_INTERRUPT


if __name__ == "__main__":
    raise SystemExit(main())
