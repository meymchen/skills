from __future__ import annotations

import hashlib
import json
import re
import shutil
import tempfile
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from deliver_github_issues.agents import invoke_agent_phase
from deliver_github_issues.audit import (
    AuditError,
    apply_satisfied_checkboxes,
    extract_checkboxes,
    validate_audit,
)
from deliver_github_issues.capabilities import validate_capabilities
from deliver_github_issues.commands import CommandError, command_json, run_command
from deliver_github_issues.contracts import ContractError, load_policy, load_queue
from deliver_github_issues.metadata import delivery_metadata
from deliver_github_issues.preview import render_preview
from deliver_github_issues.selection import (
    SelectionError,
    resolve_all_ready_issues,
    resolve_issue_selection,
)
from deliver_github_issues.state import load_state, save_state

PREFLIGHT = 10
IMPLEMENTATION = 20
CI = 30
ACCEPTANCE = 40
DRIFT = 50
INTERRUPTED = 130
RUN_ID = re.compile(r"^[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}$")


class WorkflowError(RuntimeError):
    def __init__(self, message: str, exit_code: int = PREFLIGHT) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def repository_root(cwd: Path | None = None) -> Path:
    try:
        result = run_command("git", ["rev-parse", "--show-toplevel"], cwd=cwd)
    except CommandError as error:
        raise WorkflowError("Run this skill from inside a Git repository.") from error
    if not result.output.strip():
        raise WorkflowError("Run this skill from inside a Git repository.")
    return Path(result.output.strip()).resolve()


def _config_path(root: Path, config: str) -> Path:
    path = Path(config)
    return path if path.is_absolute() else root / path


def _preview_capabilities(root: Path, primary_agent: str, metadata_agent: str) -> None:
    try:
        with tempfile.TemporaryDirectory(prefix="deliver-preview-") as temporary_name:
            validate_capabilities(
                {"primary": primary_agent, "metadata": metadata_agent},
                root,
                Path(temporary_name) / "capabilities.log",
            )
    except CommandError as error:
        raise WorkflowError(str(error), PREFLIGHT) from error


def _sort_queue(queue: dict[str, Any], ready_label: str) -> dict[str, Any]:
    instructions = {item["number"]: item for item in queue["issues"]}
    selected = resolve_issue_selection(
        ",".join(str(item["number"]) for item in queue["issues"]), ready_label
    )
    selected["issues"] = [
        {**instructions[item["number"]], "bodyHash": item["bodyHash"]}
        for item in selected["issues"]
    ]
    if selected["repository"] != queue["repository"]:
        raise SelectionError(
            f"Repository mismatch: queue has {queue['repository']}, GitHub has {selected['repository']}."
        )
    selected["baseBranch"] = queue["baseBranch"]
    return selected


def preview_queue(
    queue_path: Path, config: str, primary_agent: str = "codex", metadata_agent: str = "opencode"
) -> str:
    root = repository_root()
    try:
        queue = load_queue(queue_path)
        policy = load_policy(_config_path(root, config))
        queue = _sort_queue(queue, policy["readyLabel"])
    except (ContractError, SelectionError, CommandError) as error:
        raise WorkflowError(str(error)) from error
    _preview_capabilities(root, primary_agent, metadata_agent)
    return render_preview(queue, policy, primary_agent, metadata_agent)


def preview_issues(
    selector: str, config: str, primary_agent: str = "codex", metadata_agent: str = "opencode"
) -> str:
    root = repository_root()
    try:
        policy = load_policy(_config_path(root, config))
        queue = resolve_issue_selection(selector, policy["readyLabel"])
    except (ContractError, SelectionError, CommandError) as error:
        raise WorkflowError(str(error)) from error
    _preview_capabilities(root, primary_agent, metadata_agent)
    return render_preview(queue, policy, primary_agent, metadata_agent)


def preview_all_ready(
    config: str, primary_agent: str = "codex", metadata_agent: str = "opencode"
) -> str:
    root = repository_root()
    try:
        policy = load_policy(_config_path(root, config))
        queue = resolve_all_ready_issues(policy["readyLabel"])
    except (ContractError, SelectionError, CommandError) as error:
        raise WorkflowError(str(error)) from error
    _preview_capabilities(root, primary_agent, metadata_agent)
    return render_preview(queue, policy, primary_agent, metadata_agent)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _new_run_id() -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{uuid.uuid4().hex[:8]}"


def _phase_exit_code(phase: str) -> int:
    if phase in {"implement", "needs_implementation", "local_gates"}:
        return IMPLEMENTATION
    if phase == "ci":
        return CI
    if phase in {"audit", "awaiting_human"}:
        return ACCEPTANCE
    if phase in {"publish", "merge"}:
        return DRIFT
    return PREFLIGHT


def _parse_remote(remote: str) -> str:
    value = re.sub(r"^https://github\.com/", "", remote)
    value = re.sub(r"^git@github\.com:", "", value)
    return re.sub(r"\.git$", "", value)


def _read_live_issue(number: int, run_dir: Path) -> dict[str, Any]:
    return command_json(
        run_command(
            "gh",
            [
                "issue",
                "view",
                str(number),
                "--json",
                "number,title,body,labels,updatedAt,state,comments,url",
            ],
            log_path=run_dir / "commands.log",
        ),
        "gh issue view",
    )


def _preflight(
    queue: dict[str, Any],
    policy: dict[str, Any],
    agents: dict[str, str],
    root: Path,
    run_dir: Path,
) -> dict[str, str]:
    commands = [agents["primary"], agents["metadata"], "git", "gh"]
    commands.extend(check["command"] for check in policy["localChecks"])
    for command in dict.fromkeys(commands):
        if shutil.which(command) is None:
            raise WorkflowError(f"Required command is unavailable: {command}", PREFLIGHT)
    log = run_dir / "preflight.log"
    run_command("gh", ["auth", "status"], log_path=log)
    repository = command_json(
        run_command(
            "gh", ["repo", "view", "--json", "nameWithOwner,squashMergeAllowed"], log_path=log
        ),
        "gh repo view",
    )
    if repository["nameWithOwner"] != queue["repository"]:
        raise WorkflowError(
            f"Repository mismatch: expected {queue['repository']}, got {repository['nameWithOwner']}",
            PREFLIGHT,
        )
    if not repository["squashMergeAllowed"]:
        raise WorkflowError("Repository does not allow squash merges.", PREFLIGHT)
    branch = run_command("git", ["branch", "--show-current"], log_path=log).output.strip()
    if branch != queue["baseBranch"]:
        raise WorkflowError(f"Current branch must be {queue['baseBranch']}.", PREFLIGHT)
    if run_command("git", ["status", "--porcelain"], log_path=log).output:
        raise WorkflowError("Working tree must be clean.", PREFLIGHT)
    git_dir_text = run_command("git", ["rev-parse", "--git-dir"], log_path=log).output.strip()
    git_dir = Path(git_dir_text)
    if not git_dir.is_absolute():
        git_dir = root / git_dir
    if any(
        (git_dir / marker).exists() for marker in ("MERGE_HEAD", "rebase-merge", "rebase-apply")
    ):
        raise WorkflowError("A merge or rebase is in progress.", PREFLIGHT)
    remote = run_command("git", ["remote", "get-url", "origin"], log_path=log).output.strip()
    if _parse_remote(remote) != queue["repository"]:
        raise WorkflowError(
            f"origin mismatch: expected {queue['repository']}, got {_parse_remote(remote)}",
            PREFLIGHT,
        )
    for item in queue["issues"]:
        issue = command_json(
            run_command(
                "gh",
                ["issue", "view", str(item["number"]), "--json", "state,labels"],
                log_path=log,
            ),
            "gh issue view",
        )
        if issue["state"] != "OPEN":
            raise WorkflowError(f"Issue #{item['number']} is not open.", PREFLIGHT)
        if policy["readyLabel"] not in {label["name"] for label in issue["labels"]}:
            raise WorkflowError(f"Issue #{item['number']} lacks {policy['readyLabel']}.", PREFLIGHT)
    return validate_capabilities(agents, root, log, dynamic=True)


def _assert_branch_available(branch: str, log: Path) -> None:
    local = run_command(
        "git",
        ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
        log_path=log,
        allow_failure=True,
    )
    if local.exit_code == 0:
        raise WorkflowError(f"Local branch already exists: {branch}", PREFLIGHT)
    remote = run_command(
        "git",
        ["ls-remote", "--exit-code", "--heads", "origin", f"refs/heads/{branch}"],
        log_path=log,
        allow_failure=True,
    )
    if remote.exit_code == 0:
        raise WorkflowError(f"Remote branch already exists: {branch}", PREFLIGHT)
    pull_requests = command_json(
        run_command(
            "gh",
            ["pr", "list", "--state", "all", "--head", branch, "--json", "number"],
            log_path=log,
        ),
        "gh pr list",
    )
    if pull_requests:
        raise WorkflowError(f"A PR already exists for {branch}.", PREFLIGHT)


def _assert_issue_unclaimed(root: Path, run_id: str, issue_number: int) -> None:
    runs = root / ".agent-runs" / "deliver-github-issues"
    if not runs.is_dir():
        return
    for state_path in runs.glob("*/state.json"):
        if state_path.parent.name == run_id:
            continue
        try:
            other = load_state(state_path)
        except ContractError:
            continue
        if any(item["number"] == issue_number for item in other["issues"][other["index"] :]):
            raise WorkflowError(
                f"Issue #{issue_number} is already claimed by run {other['runId']}.", PREFLIGHT
            )


def _assert_issue_ready(issue: dict[str, Any], ready_label: str, number: int) -> None:
    if issue["state"] != "OPEN":
        raise WorkflowError(f"Issue #{number} is not open.", DRIFT)
    if ready_label not in {label["name"] for label in issue["labels"]}:
        raise WorkflowError(f"Issue #{number} lacks {ready_label}.", DRIFT)


def _assert_primary_side_effect_free(
    current: dict[str, Any], root: Path, run_dir: Path, log: Path, refs_before: str
) -> None:
    branch = run_command("git", ["branch", "--show-current"], cwd=root, log_path=log).output
    if branch.strip() != current["branch"]:
        raise WorkflowError("Primary agent switched away from the delivery branch.", DRIFT)
    if _read_live_issue(current["number"], run_dir)["updatedAt"] != current["issueUpdatedAt"]:
        raise WorkflowError("Primary agent changed the Issue.", DRIFT)
    refs_after = _protected_refs(current["branch"], root, log)
    if refs_after != refs_before:
        raise WorkflowError("Primary agent changed a protected Git ref.", DRIFT)
    remote = run_command(
        "git",
        ["ls-remote", "--exit-code", "--heads", "origin", f"refs/heads/{current['branch']}"],
        cwd=root,
        log_path=log,
        allow_failure=True,
    )
    if remote.exit_code == 0 and current["prNumber"] is None:
        raise WorkflowError("Primary agent pushed the delivery branch.", DRIFT)
    pull_requests = command_json(
        run_command(
            "gh",
            ["pr", "list", "--state", "all", "--head", current["branch"], "--json", "number"],
            cwd=root,
            log_path=log,
        ),
        "gh pr list",
    )
    allowed = {current["prNumber"]} if current["prNumber"] is not None else set()
    if {pull_request["number"] for pull_request in pull_requests} - allowed:
        raise WorkflowError("Primary agent created a pull request.", DRIFT)


def _protected_refs(branch: str, root: Path, log: Path) -> str:
    refs = run_command(
        "git",
        [
            "for-each-ref",
            "--format=%(refname) %(objectname)",
            "refs/heads",
            "refs/remotes/origin",
        ],
        cwd=root,
        log_path=log,
    ).output.splitlines()
    return "\n".join(sorted(line for line in refs if not line.startswith(f"refs/heads/{branch} ")))


def _local_gates(
    state: dict[str, Any],
    policy: dict[str, Any],
    run_dir: Path,
    save: Callable[[], None],
) -> None:
    state["current"]["localChecks"] = []
    for check in policy["localChecks"]:
        log_path = run_dir / f"{state['current']['number']}-local-{check['name']}.log"
        result = run_command(
            check["command"],
            check["arguments"],
            log_path=log_path,
            allow_failure=True,
        )
        with log_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(f"exit={result.exit_code}\n")
        state["current"]["localChecks"].append(
            {
                "name": check["name"],
                "command": result.command_line,
                "exitCode": result.exit_code,
                "log": str(log_path),
            }
        )
        save()
        if result.exit_code:
            raise WorkflowError(f"Local check failed: {check['name']}", IMPLEMENTATION)


def _wait_for_checks(
    pr_number: int,
    head_sha: str,
    expected: list[str],
    run_dir: Path,
    timeout_minutes: int,
) -> list[dict[str, Any]]:
    deadline = datetime.now(UTC) + timedelta(minutes=timeout_minutes)
    while True:
        pull_request = command_json(
            run_command(
                "gh",
                [
                    "pr",
                    "view",
                    str(pr_number),
                    "--json",
                    "headRefOid,isDraft,mergeStateStatus,reviewDecision,state,url",
                ],
                log_path=run_dir / "ci.log",
            ),
            "gh pr view",
        )
        if pull_request["headRefOid"] != head_sha:
            raise WorkflowError("PR head changed after local testing.", DRIFT)
        if (
            pull_request["isDraft"]
            or pull_request["mergeStateStatus"] == "DIRTY"
            or pull_request["reviewDecision"] == "CHANGES_REQUESTED"
        ):
            raise WorkflowError("PR is draft, conflicted, or has changes requested.", CI)
        result = run_command(
            "gh",
            ["pr", "checks", str(pr_number), "--json", "name,state,link,bucket"],
            log_path=run_dir / "ci.log",
            allow_failure=True,
        )
        checks = command_json(result, "gh pr checks") if result.output else []
        by_name = {check["name"]: check for check in checks}
        failed = [
            name
            for name in expected
            if name in by_name and by_name[name]["bucket"] in {"fail", "cancel"}
        ]
        if failed:
            raise WorkflowError("CI failed: " + ", ".join(failed), CI)
        if all(name in by_name and by_name[name]["bucket"] == "pass" for name in expected):
            return checks
        if datetime.now(UTC) >= deadline:
            raise WorkflowError(
                "Timed out waiting for all required CI checks to appear and pass.", CI
            )
        time.sleep(15)


def _human_accepted(state: dict[str, Any], issue: dict[str, Any], log: Path) -> bool:
    live = extract_checkboxes(issue["body"])
    original = state["current"]["checkboxes"]
    if len(live) != len(original):
        return False
    if any(
        live_item["text"] != original_item["text"] or not live_item["checked"]
        for live_item, original_item in zip(live, original, strict=True)
    ):
        return False
    login = command_json(run_command("gh", ["api", "user"], log_path=log), "gh api user")["login"]
    approval = f"/accept {state['current']['testedSha']}"
    return any(
        comment["author"]["login"] == login and comment["body"].strip() == approval
        for comment in issue["comments"]
    )


class DeliveryRun:
    def __init__(self, root: Path, run_dir: Path, state: dict[str, Any]) -> None:
        self.root = root
        self.run_dir = run_dir
        self.state = state
        self.state_path = run_dir / "state.json"

    def save(self) -> None:
        self.state["updatedAt"] = _utc_now()
        save_state(self.state, self.state_path)

    @property
    def log(self) -> Path:
        return self.run_dir / "commands.log"

    def failure_summary(self) -> str:
        current = self.state["current"]
        if current is None:
            return ""
        lines = [
            f"Issue #{current['number']}; phase={self.state['phase']}; "
            f"head={current['testedSha']}; PR={current['prUrl']}"
        ]
        audit = current["audit"]
        if audit is not None:
            for criterion in audit["criteria"]:
                if criterion["status"] == "satisfied":
                    evidence = "; ".join(
                        f"{item['kind']}={item['value']}" for item in criterion["evidence"]
                    )
                    lines.append(f"Evidence [satisfied]: {criterion['text']}: {evidence}")
                else:
                    lines.append(f"Unchecked [{criterion['status']}]: {criterion['text']}")
        else:
            for criterion in current["checkboxes"]:
                if not criterion["checked"]:
                    lines.append(f"Unchecked [not audited]: {criterion['text']}")
        for check in current["localChecks"]:
            status = "passed" if check["exitCode"] == 0 else "failed"
            lines.append(f"Local check [{status}]: {check['command']}: exit {check['exitCode']}")
        return "\n".join(lines)

    def schedule_fix(self, reason: str, exit_code: int) -> None:
        current = self.state["current"]
        current["fixAttempts"] += 1
        maximum = self.state["policy"]["maxPrimaryFixAttempts"]
        if current["fixAttempts"] > maximum:
            raise WorkflowError(
                f"Primary fix limit reached after {maximum} attempt(s): {reason}", exit_code
            )
        self.state["issues"][self.state["index"]]["instruction"] = reason
        self.state["phase"] = "needs_implementation"
        self.save()

    def write_success_summary(self) -> None:
        expires = None
        if not self.state["keepRunSummary"]:
            expires = (datetime.now(UTC) + timedelta(days=30)).isoformat().replace("+00:00", "Z")
        summary = {
            "version": 1,
            "runId": self.state["runId"],
            "repository": self.state["repository"],
            "agents": self.state["agents"],
            "issues": self.state["completedIssues"],
            "completedAt": _utc_now(),
            "expiresAt": expires,
        }
        summaries = self.run_dir.parent / "summaries"
        summaries.mkdir(parents=True, exist_ok=True)
        (summaries / f"{self.state['runId']}.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
            newline="\n",
        )

    def run(self) -> int:
        policy = self.state["policy"]
        while self.state["index"] < len(self.state["issues"]):
            item = self.state["issues"][self.state["index"]]
            number = item["number"]
            branch = f"{policy['branchPrefix']}{number}"
            if self.state["phase"] == "prepare":
                _assert_issue_unclaimed(self.root, self.state["runId"], number)
                run_command("git", ["fetch", "--prune", "origin"], log_path=self.log)
                run_command("git", ["switch", self.state["baseBranch"]], log_path=self.log)
                run_command(
                    "git",
                    ["merge", "--ff-only", f"origin/{self.state['baseBranch']}"],
                    log_path=self.log,
                )
                _assert_branch_available(branch, self.log)
                run_command("git", ["switch", "-c", branch], log_path=self.log)
                base_sha = run_command(
                    "git", ["rev-parse", "HEAD"], log_path=self.log
                ).output.strip()
                issue = _read_live_issue(number, self.run_dir)
                _assert_issue_ready(issue, policy["readyLabel"], number)
                if hashlib.sha256(issue["body"].encode("utf-8")).hexdigest() != item["bodyHash"]:
                    raise WorkflowError(f"Issue #{number} body changed after selection.", DRIFT)
                self.state["current"] = {
                    "number": number,
                    "title": issue["title"],
                    "branch": branch,
                    "baseSha": base_sha,
                    "fixAttempts": 0,
                    "issueUpdatedAt": issue["updatedAt"],
                    "issueUrl": issue["url"],
                    "checkboxes": extract_checkboxes(issue["body"]),
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
                self.state["phase"] = "implement"
                self.save()
            if self.state["phase"] in {"implement", "needs_implementation"}:
                issue = _read_live_issue(number, self.run_dir)
                if self.state["phase"] == "needs_implementation":
                    self.state["current"]["issueUpdatedAt"] = issue["updatedAt"]
                    self.save()
                refs_before = _protected_refs(branch, self.root, self.log)
                result = invoke_agent_phase(
                    "implement", policy, self.state, item, issue, self.run_dir, self.root
                )
                _assert_primary_side_effect_free(
                    self.state["current"], self.root, self.run_dir, self.log, refs_before
                )
                required_skills = [*item["skills"], "code-review"]
                missing = [
                    skill
                    for skill in dict.fromkeys(required_skills)
                    if skill not in result["usedSkills"]
                ]
                if result["status"] != "completed" or missing:
                    raise WorkflowError(
                        "Implementation blocked; missing skills: "
                        + ", ".join(missing)
                        + "; "
                        + "; ".join(result["blockers"]),
                        IMPLEMENTATION,
                    )
                dirty = run_command("git", ["status", "--porcelain"], log_path=self.log).output
                head = run_command("git", ["rev-parse", "HEAD"], log_path=self.log).output.strip()
                changed_files = set(
                    run_command(
                        "git",
                        ["diff", "--name-only", self.state["current"]["baseSha"]],
                        log_path=self.log,
                    ).output.splitlines()
                )
                changed_files.update(
                    run_command(
                        "git",
                        ["ls-files", "--others", "--exclude-standard"],
                        log_path=self.log,
                    ).output.splitlines()
                )
                if not set(result["changedFiles"]) <= changed_files:
                    raise WorkflowError(
                        "Primary handoff changedFiles differs from the Git diff.", IMPLEMENTATION
                    )
                # Git is the source of truth: fix rounds legitimately report only the
                # files touched in that round, not the cumulative branch diff.
                result["changedFiles"] = sorted(changed_files)
                reported_sha = result["commitSha"]
                if reported_sha is not None and not (
                    len(reported_sha) >= 7 and head.startswith(reported_sha)
                ):
                    raise WorkflowError(
                        "Primary handoff commitSha differs from HEAD.", IMPLEMENTATION
                    )
                if not dirty and head == self.state["current"]["baseSha"]:
                    raise WorkflowError("Implementation produced no changes.", IMPLEMENTATION)
                if dirty:
                    run_command("git", ["add", "--all"], log_path=self.log)
                    run_command(
                        "git",
                        ["commit", "-m", f"Provisional implementation for #{number}"],
                        log_path=self.log,
                    )
                self.state["current"]["implementation"] = result
                # Refresh local checks before review so the reviewer never judges a
                # stale failing log from an earlier round (review itself is read-only).
                try:
                    _local_gates(self.state, policy, self.run_dir, self.save)
                except WorkflowError as error:
                    self.schedule_fix(str(error), IMPLEMENTATION)
                    continue
                self.state["phase"] = "review"
                self.save()
            if self.state["phase"] == "review":
                issue = _read_live_issue(number, self.run_dir)
                refs_before = _protected_refs(branch, self.root, self.log)
                review = invoke_agent_phase(
                    "review", policy, self.state, item, issue, self.run_dir, self.root
                )
                _assert_primary_side_effect_free(
                    self.state["current"], self.root, self.run_dir, self.log, refs_before
                )
                self.state["current"]["review"] = review
                if review["status"] != "passed" or "code-review" not in review["usedSkills"]:
                    self.schedule_fix(
                        "Code review requires changes: " + "; ".join(review["findings"]),
                        IMPLEMENTATION,
                    )
                    continue
                self.state["phase"] = "local_gates"
                self.save()
            if self.state["phase"] == "local_gates":
                try:
                    _local_gates(self.state, policy, self.run_dir, self.save)
                except WorkflowError as error:
                    self.schedule_fix(str(error), IMPLEMENTATION)
                    continue
                self.state["current"]["metadata"] = delivery_metadata(
                    policy, self.state, self.run_dir
                )
                title = self.state["current"]["metadata"]["commitTitle"]
                run_command(
                    "git",
                    ["reset", "--soft", self.state["current"]["baseSha"]],
                    log_path=self.log,
                )
                run_command("git", ["add", "--all"], log_path=self.log)
                run_command("git", ["commit", "-m", title], log_path=self.log)
                self.state["current"]["testedSha"] = run_command(
                    "git", ["rev-parse", "HEAD"], log_path=self.log
                ).output.strip()
                try:
                    _local_gates(self.state, policy, self.run_dir, self.save)
                except WorkflowError as error:
                    self.schedule_fix(f"Final commit verification failed: {error}", IMPLEMENTATION)
                    continue
                self.state["phase"] = "publish"
                self.save()
            if self.state["phase"] == "publish":
                current = self.state["current"]
                if current["prNumber"] is None:
                    run_command(
                        "git", ["push", "--set-upstream", "origin", branch], log_path=self.log
                    )
                    body_path = self.run_dir / f"{number}-pr-body.md"
                    tests = "\n".join(
                        f"- `{check['command']}`: exit {check['exitCode']}"
                        for check in current["localChecks"]
                    )
                    body_path.write_text(
                        f"{current['metadata']['summary']}\n\n## Verification\n\n{tests}\n\nCloses #{number}\n",
                        encoding="utf-8",
                        newline="\n",
                    )
                    url = run_command(
                        "gh",
                        [
                            "pr",
                            "create",
                            "--title",
                            current["metadata"]["prTitle"],
                            "--body-file",
                            str(body_path),
                            "--base",
                            self.state["baseBranch"],
                            "--head",
                            branch,
                        ],
                        log_path=self.log,
                    ).output.strip()
                    pull_request = command_json(
                        run_command(
                            "gh",
                            ["pr", "view", url, "--json", "number,url,headRefOid"],
                            log_path=self.log,
                        ),
                        "gh pr view",
                    )
                else:
                    run_command(
                        "git", ["push", "--force-with-lease", "origin", branch], log_path=self.log
                    )
                    pull_request = command_json(
                        run_command(
                            "gh",
                            [
                                "pr",
                                "view",
                                str(current["prNumber"]),
                                "--json",
                                "number,url,headRefOid",
                            ],
                            log_path=self.log,
                        ),
                        "gh pr view",
                    )
                if pull_request["headRefOid"] != current["testedSha"]:
                    raise WorkflowError("PR head differs from tested commit.", DRIFT)
                current["prNumber"] = pull_request["number"]
                current["prUrl"] = pull_request["url"]
                self.state["phase"] = "ci"
                self.save()
            if self.state["phase"] == "ci":
                current = self.state["current"]
                try:
                    current["ciChecks"] = _wait_for_checks(
                        current["prNumber"],
                        current["testedSha"],
                        policy["requiredChecks"],
                        self.run_dir,
                        policy["ciTimeoutMinutes"],
                    )
                except WorkflowError as error:
                    if str(error).startswith("CI failed:"):
                        self.schedule_fix(str(error), CI)
                        continue
                    raise
                self.state["phase"] = "audit"
                self.save()
            if self.state["phase"] == "audit":
                current = self.state["current"]
                issue = _read_live_issue(number, self.run_dir)
                if issue["updatedAt"] != current["issueUpdatedAt"]:
                    raise WorkflowError("Issue changed after the implementation snapshot.", DRIFT)
                refs_before = _protected_refs(branch, self.root, self.log)
                audit = invoke_agent_phase(
                    "audit", policy, self.state, item, issue, self.run_dir, self.root
                )
                _assert_primary_side_effect_free(
                    self.state["current"], self.root, self.run_dir, self.log, refs_before
                )
                validate_audit(
                    audit,
                    current["checkboxes"],
                    self.root,
                    [
                        check["command"]
                        for check in current["localChecks"]
                        if check["exitCode"] == 0
                    ],
                    [check["link"] for check in current["ciChecks"] if check["bucket"] == "pass"],
                )
                current["audit"] = audit
                updated_body = apply_satisfied_checkboxes(issue["body"], audit)
                if updated_body != issue["body"]:
                    body_path = self.run_dir / f"{number}-issue-body.md"
                    body_path.write_text(updated_body, encoding="utf-8", newline="\n")
                    if (
                        _read_live_issue(number, self.run_dir)["updatedAt"]
                        != current["issueUpdatedAt"]
                    ):
                        raise WorkflowError("Issue changed before checkbox update.", DRIFT)
                    run_command(
                        "gh",
                        ["issue", "edit", str(number), "--body-file", str(body_path)],
                        log_path=self.log,
                    )
                comment_path = self.run_dir / f"{number}-audit-comment.md"
                rows = "\n".join(
                    f"- [{criterion['status']}] {criterion['text']}: "
                    + "; ".join(f"{e['kind']}={e['value']}" for e in criterion["evidence"])
                    for criterion in audit["criteria"]
                )
                comment_path.write_text(
                    f"Acceptance audit for `{current['testedSha']}` in {current['prUrl']}.\n\n{rows}\n",
                    encoding="utf-8",
                    newline="\n",
                )
                run_command(
                    "gh",
                    ["issue", "comment", str(number), "--body-file", str(comment_path)],
                    log_path=self.log,
                )
                statuses = {criterion["status"] for criterion in audit["criteria"]}
                if "unsatisfied" in statuses:
                    self.schedule_fix(
                        "Acceptance found implementation gaps: "
                        + "; ".join(
                            criterion["text"]
                            for criterion in audit["criteria"]
                            if criterion["status"] == "unsatisfied"
                        ),
                        ACCEPTANCE,
                    )
                    continue
                if "human_required" in statuses:
                    self.state["phase"] = "awaiting_human"
                    self.save()
                    raise WorkflowError(
                        "Human acceptance is required; check remaining boxes and comment /accept <head-sha>.",
                        ACCEPTANCE,
                    )
                self.state["phase"] = "merge"
                self.save()
            if self.state["phase"] == "awaiting_human":
                current = self.state["current"]
                if not _human_accepted(
                    self.state, _read_live_issue(number, self.run_dir), self.log
                ):
                    raise WorkflowError(
                        "Human acceptance is incomplete or does not match the tested SHA.",
                        ACCEPTANCE,
                    )
                current["ciChecks"] = _wait_for_checks(
                    current["prNumber"],
                    current["testedSha"],
                    policy["requiredChecks"],
                    self.run_dir,
                    1,
                )
                self.state["phase"] = "merge"
                self.save()
            if self.state["phase"] == "merge":
                current = self.state["current"]
                pull_request = command_json(
                    run_command(
                        "gh",
                        [
                            "pr",
                            "view",
                            str(current["prNumber"]),
                            "--json",
                            "headRefOid,isDraft,mergeStateStatus,reviewDecision,state",
                        ],
                        log_path=self.log,
                    ),
                    "gh pr view",
                )
                if pull_request["headRefOid"] != current["testedSha"]:
                    raise WorkflowError("PR head drifted before merge.", DRIFT)
                if pull_request["state"] != "MERGED":
                    if (
                        pull_request["isDraft"]
                        or pull_request["mergeStateStatus"] == "DIRTY"
                        or pull_request["reviewDecision"]
                        in {"CHANGES_REQUESTED", "REVIEW_REQUIRED"}
                    ):
                        raise WorkflowError("PR still requires human action.", ACCEPTANCE)
                    run_command(
                        "gh",
                        [
                            "pr",
                            "merge",
                            str(current["prNumber"]),
                            "--squash",
                            "--match-head-commit",
                            current["testedSha"],
                            "--subject",
                            current["metadata"]["prTitle"],
                        ],
                        log_path=self.log,
                    )
                merged = command_json(
                    run_command(
                        "gh",
                        ["pr", "view", str(current["prNumber"]), "--json", "state,mergeCommit"],
                        log_path=self.log,
                    ),
                    "gh pr view",
                )
                closed = _read_live_issue(number, self.run_dir)
                if merged["state"] != "MERGED" or closed["state"] != "CLOSED":
                    raise WorkflowError("Merge or issue closure verification failed.", DRIFT)
                remote = run_command(
                    "git",
                    ["ls-remote", "--exit-code", "--heads", "origin", f"refs/heads/{branch}"],
                    log_path=self.log,
                    allow_failure=True,
                )
                if remote.exit_code == 0:
                    run_command("git", ["push", "origin", "--delete", branch], log_path=self.log)
                run_command("git", ["switch", self.state["baseBranch"]], log_path=self.log)
                run_command("git", ["fetch", "--prune", "origin"], log_path=self.log)
                run_command(
                    "git",
                    ["merge", "--ff-only", f"origin/{self.state['baseBranch']}"],
                    log_path=self.log,
                )
                ancestor = run_command(
                    "git",
                    ["merge-base", "--is-ancestor", merged["mergeCommit"]["oid"], "HEAD"],
                    log_path=self.log,
                    allow_failure=True,
                )
                if ancestor.exit_code:
                    raise WorkflowError("Squash merge commit is not on local base branch.", DRIFT)
                run_command("git", ["branch", "-D", branch], log_path=self.log)
                if run_command("git", ["status", "--porcelain"], log_path=self.log).output:
                    raise WorkflowError("Working tree is not clean after cleanup.", DRIFT)
                if (
                    run_command(
                        "git",
                        ["ls-remote", "--exit-code", "--heads", "origin", f"refs/heads/{branch}"],
                        log_path=self.log,
                        allow_failure=True,
                    ).exit_code
                    == 0
                ):
                    raise WorkflowError("Remote branch still exists after cleanup.", DRIFT)
                if (
                    run_command(
                        "git",
                        ["show-ref", "--verify", "--quiet", f"refs/remotes/origin/{branch}"],
                        log_path=self.log,
                        allow_failure=True,
                    ).exit_code
                    == 0
                ):
                    raise WorkflowError("Remote-tracking branch still exists after cleanup.", DRIFT)
                self.state["completedIssues"].append(
                    {
                        "number": number,
                        "testedSha": current["testedSha"],
                        "prNumber": current["prNumber"],
                        "prUrl": current["prUrl"],
                        "localChecks": current["localChecks"],
                        "ciChecks": current["ciChecks"],
                        "audit": current["audit"],
                        "fixAttempts": current["fixAttempts"],
                        "completedAt": _utc_now(),
                    }
                )
                self.state["index"] += 1
                self.state["current"] = None
                self.state["phase"] = (
                    "prepare" if self.state["index"] < len(self.state["issues"]) else "complete"
                )
                self.save()
        completed = self.run_dir.resolve()
        runs_root = (self.root / ".agent-runs" / "deliver-github-issues").resolve()
        if completed.parent != runs_root or completed.name != self.state["runId"]:
            raise WorkflowError("Refusing to remove an invalid run directory.", DRIFT)
        self.write_success_summary()
        shutil.rmtree(completed)
        return len(self.state["issues"])


def _start_delivery(
    root: Path,
    queue: dict[str, Any],
    policy: dict[str, Any],
    primary_agent: str,
    metadata_agent: str,
    keep_run_summary: bool,
) -> DeliveryRun:
    run_id = _new_run_id()
    run_dir = root / ".agent-runs" / "deliver-github-issues" / run_id
    run_dir.mkdir(parents=True)
    state = {
        "version": 1,
        "runId": run_id,
        "repository": queue["repository"],
        "baseBranch": queue["baseBranch"],
        "policy": policy,
        "agents": {"primary": primary_agent, "metadata": metadata_agent, "versions": {}},
        "keepRunSummary": keep_run_summary,
        "issues": queue["issues"],
        "completedIssues": [],
        "index": 0,
        "phase": "preflight",
        "current": None,
        "updatedAt": _utc_now(),
    }
    delivery = DeliveryRun(root, run_dir, state)
    delivery.save()
    try:
        state["agents"]["versions"] = _preflight(queue, policy, state["agents"], root, run_dir)
    except (WorkflowError, CommandError, OSError, KeyError, TypeError) as error:
        exit_code = error.exit_code if isinstance(error, WorkflowError) else PREFLIGHT
        raise WorkflowError(f"{error}\nRun state preserved at {run_dir}", exit_code) from error
    state["phase"] = "prepare"
    delivery.save()
    return delivery


def _new_delivery(
    queue_path: Path,
    config: str,
    primary_agent: str,
    metadata_agent: str,
    keep_run_summary: bool,
) -> DeliveryRun:
    root = repository_root()
    try:
        queue = load_queue(queue_path)
        policy = load_policy(_config_path(root, config))
        queue = _sort_queue(queue, policy["readyLabel"])
    except (ContractError, SelectionError, CommandError) as error:
        raise WorkflowError(str(error), PREFLIGHT) from error
    return _start_delivery(root, queue, policy, primary_agent, metadata_agent, keep_run_summary)


def _new_issue_delivery(
    selector: str,
    config: str,
    primary_agent: str,
    metadata_agent: str,
    keep_run_summary: bool,
) -> DeliveryRun:
    root = repository_root()
    try:
        policy = load_policy(_config_path(root, config))
        queue = resolve_issue_selection(selector, policy["readyLabel"])
    except (ContractError, SelectionError, CommandError) as error:
        raise WorkflowError(str(error), PREFLIGHT) from error
    return _start_delivery(root, queue, policy, primary_agent, metadata_agent, keep_run_summary)


def _new_all_ready_delivery(
    config: str, primary_agent: str, metadata_agent: str, keep_run_summary: bool
) -> DeliveryRun:
    root = repository_root()
    try:
        policy = load_policy(_config_path(root, config))
        queue = resolve_all_ready_issues(policy["readyLabel"])
    except (ContractError, SelectionError, CommandError) as error:
        raise WorkflowError(str(error), PREFLIGHT) from error
    return _start_delivery(root, queue, policy, primary_agent, metadata_agent, keep_run_summary)


def _execute_new(factory: Callable[[], DeliveryRun]) -> int:
    delivery: DeliveryRun | None = None
    try:
        delivery = factory()
        return delivery.run()
    except KeyboardInterrupt as error:
        if delivery:
            delivery.save()
            raise WorkflowError(
                f"Interrupted. Run state preserved at {delivery.run_dir}", INTERRUPTED
            ) from error
        raise WorkflowError("Interrupted.", INTERRUPTED) from error
    except WorkflowError as error:
        if delivery and delivery.run_dir.exists() and "Run state preserved at" not in str(error):
            summary = delivery.failure_summary()
            detail = f"\n{summary}" if summary else ""
            raise WorkflowError(
                f"{error}{detail}\nRun state preserved at {delivery.run_dir}", error.exit_code
            ) from error
        raise
    except (CommandError, ContractError, AuditError, OSError, KeyError, TypeError) as error:
        code = _phase_exit_code(delivery.state["phase"]) if delivery else PREFLIGHT
        summary = delivery.failure_summary() if delivery else ""
        detail = f"\n{summary}" if summary else ""
        suffix = f"\nRun state preserved at {delivery.run_dir}" if delivery else ""
        raise WorkflowError(f"{error}{detail}{suffix}", code) from error


def execute_delivery(
    queue_path: Path,
    config: str,
    primary_agent: str = "codex",
    metadata_agent: str = "opencode",
    keep_run_summary: bool = False,
) -> int:
    return _execute_new(
        lambda: _new_delivery(queue_path, config, primary_agent, metadata_agent, keep_run_summary)
    )


def execute_issues(
    selector: str,
    config: str,
    primary_agent: str = "codex",
    metadata_agent: str = "opencode",
    keep_run_summary: bool = False,
) -> int:
    return _execute_new(
        lambda: _new_issue_delivery(
            selector, config, primary_agent, metadata_agent, keep_run_summary
        )
    )


def execute_all_ready(
    config: str,
    primary_agent: str = "codex",
    metadata_agent: str = "opencode",
    keep_run_summary: bool = False,
) -> int:
    return _execute_new(
        lambda: _new_all_ready_delivery(config, primary_agent, metadata_agent, keep_run_summary)
    )


def clean_expired_summaries() -> int:
    root = repository_root()
    summaries = root / ".agent-runs" / "deliver-github-issues" / "summaries"
    if not summaries.is_dir():
        return 0
    now = datetime.now(UTC)
    removed = 0
    for path in summaries.glob("*.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            expires = value.get("expiresAt")
            if expires and datetime.fromisoformat(expires.replace("Z", "+00:00")) <= now:
                path.unlink()
                removed += 1
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
    return removed


def resume_delivery(run_id: str, instruction: str = "") -> int:
    if not RUN_ID.fullmatch(run_id):
        raise WorkflowError("Resume ID is invalid.", PREFLIGHT)
    root = repository_root()
    runs_root = (root / ".agent-runs" / "deliver-github-issues").resolve()
    run_dir = (runs_root / run_id).resolve()
    if run_dir.parent != runs_root:
        raise WorkflowError("Resume path escaped .agent-runs/deliver-github-issues.", PREFLIGHT)
    try:
        state = load_state(run_dir / "state.json")
    except ContractError as error:
        raise WorkflowError(str(error), PREFLIGHT) from error
    if state["runId"] != run_id:
        raise WorkflowError("Run state identity is invalid.", PREFLIGHT)
    if state["phase"] == "complete":
        raise WorkflowError("Run is already complete.", PREFLIGHT)
    delivery = DeliveryRun(root, run_dir, state)
    try:
        if state["phase"] == "preflight":
            # A run that failed during preflight must redo it; run() has no
            # preflight branch and would otherwise spin without progress.
            state["agents"]["versions"] = _preflight(
                state, state["policy"], state["agents"], root, run_dir
            )
            state["phase"] = "prepare"
            delivery.save()
        if state["current"] is not None and state["phase"] not in {"prepare", "merge"}:
            actual_branch = run_command(
                "git", ["branch", "--show-current"], log_path=delivery.log
            ).output.strip()
            if actual_branch != state["current"]["branch"]:
                raise WorkflowError(
                    f"Resume requires branch {state['current']['branch']}, got {actual_branch}.",
                    DRIFT,
                )
        if instruction:
            state["issues"][state["index"]]["instruction"] = instruction
            delivery.save()
        return delivery.run()
    except KeyboardInterrupt as error:
        delivery.save()
        raise WorkflowError(
            f"Interrupted. Run state preserved at {delivery.run_dir}", INTERRUPTED
        ) from error
    except WorkflowError as error:
        if run_dir.exists() and "Run state preserved at" not in str(error):
            summary = delivery.failure_summary()
            detail = f"\n{summary}" if summary else ""
            raise WorkflowError(
                f"{error}{detail}\nRun state preserved at {run_dir}", error.exit_code
            ) from error
        raise
    except (CommandError, ContractError, AuditError, OSError, KeyError, TypeError) as error:
        summary = delivery.failure_summary()
        detail = f"\n{summary}" if summary else ""
        raise WorkflowError(
            f"{error}{detail}\nRun state preserved at {run_dir}",
            _phase_exit_code(state["phase"]),
        ) from error
