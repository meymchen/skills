# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn
from urllib.parse import urlparse


MAX_FILES = 10_000
MAX_BYTES = 2 * 1024 * 1024 * 1024
ROOT_CACHE_NAMES = (
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".pyright",
    ".tox",
    ".nox",
    ".coverage",
    "htmlcov",
)
ROOT_CACHE_DIRECTORY_NAMES = set(ROOT_CACHE_NAMES)
PR_FIELDS = (
    "number,state,headRefName,headRefOid,baseRefName,mergeCommit,isCrossRepository,url"
)
SKIP_TREES = {".git", ".venv", "node_modules", ".agent-runs", ".scratch"}


class CleanupError(RuntimeError):
    exit_code = 1


class SafetyError(CleanupError):
    exit_code = 2


class UsageError(CleanupError):
    exit_code = 64


class InterruptedCleanup(CleanupError):
    exit_code = 130


class ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(64, f"{self.prog}: error: {message}\n")


@dataclass(frozen=True)
class RepositoryInfo:
    root: Path
    name_with_owner: str
    default_branch: str
    remote: str
    host: str


@dataclass(frozen=True)
class PullRequestInfo:
    number: int
    source_branch: str
    source_oid: str
    base_branch: str
    merge_oid: str
    url: str


@dataclass(frozen=True)
class CacheTarget:
    path: Path
    file_count: int
    byte_count: int


@dataclass
class Progress:
    fetched: bool = False
    cache_files: int = 0
    cache_bytes: int = 0
    default_updated: bool = False
    deleted_branches: list[tuple[str, str]] | None = None

    def __post_init__(self) -> None:
        if self.deleted_branches is None:
            self.deleted_branches = []


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = ArgumentParser(
        prog="cleanup-merged-branch",
        description="Clean a merged local branch and update the default branch safely.",
    )
    parser.add_argument("--pr", type=int, metavar="NUMBER")
    parser.add_argument("--remote", metavar="NAME")
    parser.add_argument("--all-stale", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.pr is not None and args.pr <= 0:
        raise UsageError("--pr must be a positive GitHub pull request number.")
    return args


def run(
    command: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        input=input_text,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "command failed"
        raise CleanupError(f"{command[0]} failed: {detail}")
    return result


def git(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run(["git", *args], cwd=root, check=check)


def gh_prefix() -> list[str]:
    override = os.environ.get("CLEANUP_MERGED_BRANCH_GH")
    if override:
        path = Path(override)
        return [sys.executable, str(path)] if path.suffix == ".py" else [override]
    executable = shutil.which("gh")
    if executable is None:
        raise SafetyError("Required tool `gh` was not found.")
    return [executable]


def gh_json(root: Path, *args: str) -> Any:
    result = run([*gh_prefix(), *args], cwd=root)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise CleanupError(f"gh returned invalid JSON: {error}") from error


def ensure_tools() -> None:
    if shutil.which("git") is None:
        raise SafetyError("Required tool `git` was not found.")
    gh_prefix()


def repository_root() -> Path:
    result = run(["git", "rev-parse", "--show-toplevel"])
    return Path(result.stdout.strip()).resolve()


def parse_remote_identity(url: str) -> tuple[str, str]:
    value = url.strip()
    if "://" not in value and "@" in value and ":" in value:
        user_host, path = value.split(":", 1)
        host = user_host.rsplit("@", 1)[-1]
    else:
        parsed = urlparse(value)
        host = parsed.hostname or ""
        path = parsed.path.lstrip("/")
    repository = path.removesuffix(".git").strip("/")
    if not host or repository.count("/") != 1:
        raise SafetyError(
            f"Remote URL cannot be mapped to one GitHub repository: {url}"
        )
    return host.lower(), repository


def current_branch(root: Path) -> str:
    result = git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if result.returncode != 0:
        raise SafetyError("Detached HEAD is not a safe cleanup starting point.")
    return result.stdout.strip()


def local_branch_oid(root: Path, branch: str) -> str | None:
    result = git(root, "rev-parse", "--verify", f"refs/heads/{branch}", check=False)
    return result.stdout.strip() if result.returncode == 0 else None


def require_clean_worktree(root: Path) -> None:
    if git(root, "status", "--porcelain=v1", "--untracked-files=all").stdout.strip():
        raise SafetyError("The current worktree has uncommitted or untracked changes.")
    git_dir = Path(git(root, "rev-parse", "--absolute-git-dir").stdout.strip())
    operation_markers = (
        git_dir / "MERGE_HEAD",
        git_dir / "REBASE_HEAD",
        git_dir / "rebase-apply",
        git_dir / "rebase-merge",
    )
    if any(marker.exists() for marker in operation_markers):
        raise SafetyError("A merge or rebase is in progress.")


def load_repository_info(root: Path, requested_remote: str | None) -> RepositoryInfo:
    payload = gh_json(root, "repo", "view", "--json", "nameWithOwner,defaultBranchRef")
    name_with_owner = str(payload.get("nameWithOwner", ""))
    default_ref = payload.get("defaultBranchRef") or {}
    default_branch = str(default_ref.get("name", ""))
    if not name_with_owner or not default_branch:
        raise SafetyError("GitHub did not report a repository and default branch.")

    remotes = git(root, "remote").stdout.splitlines()
    candidates: list[tuple[str, str]] = []
    for remote in remotes:
        if requested_remote and remote != requested_remote:
            continue
        url_result = git(root, "config", "--get", f"remote.{remote}.url", check=False)
        if url_result.returncode != 0:
            continue
        try:
            host, identity = parse_remote_identity(url_result.stdout.strip())
        except SafetyError:
            continue
        if identity.casefold() == name_with_owner.casefold():
            candidates.append((remote, host))
    if requested_remote is None and len(candidates) > 1:
        branch = current_branch(root)
        configured = git(
            root, "config", "--get", f"branch.{branch}.remote", check=False
        ).stdout.strip()
        preferred = [
            candidate for candidate in candidates if candidate[0] == configured
        ]
        if len(preferred) == 1:
            candidates = preferred
    if len(candidates) != 1:
        qualifier = f" named {requested_remote!r}" if requested_remote else ""
        raise SafetyError(
            f"Expected one remote{qualifier} matching {name_with_owner}; found {len(candidates)}."
        )
    remote, host = candidates[0]
    auth = run(
        [*gh_prefix(), "auth", "status", "--hostname", host], cwd=root, check=False
    )
    if auth.returncode != 0:
        raise SafetyError(f"gh is not authenticated for {host}.")
    return RepositoryInfo(root, name_with_owner, default_branch, remote, host)


def load_pull_request(
    repository: RepositoryInfo, number: int | None
) -> PullRequestInfo:
    command = ["pr", "view"]
    if number is not None:
        command.append(str(number))
    command.extend(("--json", PR_FIELDS))
    payload = gh_json(repository.root, *command)
    if payload.get("state") != "MERGED":
        raise SafetyError("The pull request is not merged.")
    if payload.get("isCrossRepository"):
        raise SafetyError("Cross-repository pull requests are not supported.")
    source_branch = str(payload.get("headRefName", ""))
    source_oid = str(payload.get("headRefOid", ""))
    base_branch = str(payload.get("baseRefName", ""))
    merge = payload.get("mergeCommit") or {}
    merge_oid = str(merge.get("oid", ""))
    if not all((source_branch, source_oid, base_branch, merge_oid)):
        raise SafetyError(
            "The pull request is missing branch or commit identity evidence."
        )
    if base_branch != repository.default_branch:
        raise SafetyError(
            f"PR base {base_branch!r} is not default branch {repository.default_branch!r}."
        )
    return PullRequestInfo(
        number=int(payload["number"]),
        source_branch=source_branch,
        source_oid=source_oid,
        base_branch=base_branch,
        merge_oid=merge_oid,
        url=str(payload.get("url", "")),
    )


def remote_branch_exists(repository: RepositoryInfo, branch: str) -> bool:
    result = git(
        repository.root,
        "ls-remote",
        "--heads",
        repository.remote,
        f"refs/heads/{branch}",
    )
    return bool(result.stdout.strip())


def verify_source_identity(
    repository: RepositoryInfo,
    pull_request: PullRequestInfo,
    starting_branch: str,
    explicit_pr: bool,
) -> str | None:
    if starting_branch == repository.default_branch and not explicit_pr:
        raise SafetyError("Use --pr NUMBER when starting from the default branch.")
    if starting_branch not in {repository.default_branch, pull_request.source_branch}:
        raise SafetyError(
            f"Current branch {starting_branch!r} is not PR source "
            f"{pull_request.source_branch!r}."
        )
    oid = local_branch_oid(repository.root, pull_request.source_branch)
    if oid is None:
        if not explicit_pr:
            raise SafetyError(
                "The local source branch is missing; specify --pr to rerun."
            )
        return None
    if oid != pull_request.source_oid:
        raise SafetyError(
            f"Local source tip {oid} differs from PR head {pull_request.source_oid}."
        )
    return oid


def parse_worktrees(root: Path) -> dict[str, Path]:
    result = git(root, "worktree", "list", "--porcelain")
    branches: dict[str, Path] = {}
    path: Path | None = None
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            path = Path(line.removeprefix("worktree ")).resolve()
        elif line.startswith("branch refs/heads/") and path is not None:
            branches[line.removeprefix("branch refs/heads/")] = path
    return branches


def verify_worktree_ownership(
    repository: RepositoryInfo, pull_request: PullRequestInfo
) -> None:
    worktrees = parse_worktrees(repository.root)
    for branch in (repository.default_branch, pull_request.source_branch):
        owner = worktrees.get(branch)
        if owner is not None and owner != repository.root:
            raise SafetyError(
                f"Branch {branch!r} is checked out in another worktree: {owner}"
            )


def is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def candidate_cache_paths(root: Path) -> list[Path]:
    candidates = [root / name for name in ROOT_CACHE_NAMES]
    candidates.extend(root.glob(".coverage.*"))
    for directory, names, _files in os.walk(root, topdown=True, followlinks=False):
        parent = Path(directory)
        kept: list[str] = []
        for name in names:
            path = parent / name
            if name == "__pycache__":
                candidates.append(path)
            elif (
                name not in SKIP_TREES
                and name not in ROOT_CACHE_DIRECTORY_NAMES
                and not (parent == root and name.startswith(".coverage."))
                and not is_link(path)
            ):
                kept.append(name)
        names[:] = kept
    return sorted({path for path in candidates if path.exists()}, key=str)


def validate_cache_target(
    root: Path, target: Path
) -> tuple[CacheTarget | None, str | None]:
    try:
        relative = target.relative_to(root)
        resolved = target.resolve(strict=True)
    except (OSError, ValueError) as error:
        return None, f"cannot resolve safely: {error}"
    if not resolved.is_relative_to(root) or relative == Path("."):
        return None, "target escapes the worktree"

    entries = [target]
    if target.is_dir():
        for directory, names, files in os.walk(target, followlinks=False):
            parent = Path(directory)
            entries.extend(parent / name for name in names)
            entries.extend(parent / name for name in files)
    if any(is_link(path) for path in entries):
        return None, "target contains a symbolic link or junction"
    if any(path.name == ".git" for path in entries[1:]):
        return None, "target contains a nested Git repository"

    files = [path for path in entries if path.is_file()]
    try:
        byte_count = sum(path.stat().st_size for path in files)
    except OSError as error:
        return None, f"cannot measure safely: {error}"
    if len(files) > MAX_FILES or byte_count > MAX_BYTES:
        raise SafetyError(
            f"Cache target exceeds hard limit: {len(files)} files, {byte_count} bytes."
        )

    tracked = git(root, "ls-files", "--", relative.as_posix()).stdout.strip()
    if tracked:
        return None, "target contains tracked files"
    relative_entries = [path.relative_to(root).as_posix() for path in entries]
    ignored = run(
        ["git", "check-ignore", "--stdin", "-z"],
        cwd=root,
        check=False,
        input_text="\x00".join(relative_entries) + "\x00",
    )
    ignored_entries = {item for item in ignored.stdout.split("\x00") if item}
    for item in relative_entries:
        if item not in ignored_entries:
            return None, f"{item} is not ignored"
    return CacheTarget(target, len(files), byte_count), None


def build_cache_plan(root: Path) -> tuple[list[CacheTarget], list[tuple[Path, str]]]:
    safe: list[CacheTarget] = []
    skipped: list[tuple[Path, str]] = []
    for target in candidate_cache_paths(root):
        validated, reason = validate_cache_target(root, target)
        if validated is None:
            skipped.append((target, reason or "unknown safety failure"))
        else:
            safe.append(validated)
    file_count = sum(target.file_count for target in safe)
    byte_count = sum(target.byte_count for target in safe)
    if file_count > MAX_FILES or byte_count > MAX_BYTES:
        raise SafetyError(
            f"Cache plan exceeds hard limit: {file_count} files, {byte_count} bytes."
        )
    return safe, skipped


def delete_cache_plan(targets: list[CacheTarget], progress: Progress) -> None:
    for target in targets:
        refreshed, reason = validate_cache_target(repository_root(), target.path)
        if refreshed != target:
            detail = reason or "contents changed"
            raise SafetyError(
                f"Cache target changed before deletion: {target.path} ({detail})"
            )
        if target.path.is_dir():
            shutil.rmtree(target.path)
        else:
            target.path.unlink()
        progress.cache_files += target.file_count
        progress.cache_bytes += target.byte_count


def verify_default_can_update(repository: RepositoryInfo) -> None:
    remote_ref = f"refs/remotes/{repository.remote}/{repository.default_branch}"
    if (
        git(repository.root, "show-ref", "--verify", remote_ref, check=False).returncode
        != 0
    ):
        raise SafetyError(f"Remote default branch ref is missing: {remote_ref}")
    local_oid = local_branch_oid(repository.root, repository.default_branch)
    if local_oid is None:
        return
    result = git(
        repository.root,
        "merge-base",
        "--is-ancestor",
        local_oid,
        f"{repository.remote}/{repository.default_branch}",
        check=False,
    )
    if result.returncode != 0:
        raise SafetyError("The local default branch cannot be fast-forwarded.")


def update_default_branch(repository: RepositoryInfo, progress: Progress) -> None:
    if local_branch_oid(repository.root, repository.default_branch) is None:
        git(
            repository.root,
            "switch",
            "--track",
            "-c",
            repository.default_branch,
            f"{repository.remote}/{repository.default_branch}",
        )
    elif current_branch(repository.root) != repository.default_branch:
        git(repository.root, "switch", repository.default_branch)
    git(
        repository.root,
        "pull",
        "--ff-only",
        repository.remote,
        repository.default_branch,
    )
    progress.default_updated = True


def verify_merge_present(
    repository: RepositoryInfo, pull_request: PullRequestInfo
) -> None:
    result = git(
        repository.root,
        "merge-base",
        "--is-ancestor",
        pull_request.merge_oid,
        f"refs/heads/{repository.default_branch}",
        check=False,
    )
    if result.returncode != 0:
        raise SafetyError(
            f"PR merge commit {pull_request.merge_oid} is absent from the local default branch."
        )


def delete_local_branch(
    repository: RepositoryInfo,
    pull_request: PullRequestInfo,
    progress: Progress,
) -> None:
    oid = local_branch_oid(repository.root, pull_request.source_branch)
    if oid is None:
        return
    if oid != pull_request.source_oid:
        raise SafetyError("The local source branch changed before deletion.")
    git(repository.root, "branch", "-D", pull_request.source_branch)
    assert progress.deleted_branches is not None
    progress.deleted_branches.append((pull_request.source_branch, oid))


def stale_branch_candidates(
    repository: RepositoryInfo, excluded_source_branch: str
) -> list[str]:
    worktrees = parse_worktrees(repository.root)
    result = git(
        repository.root,
        "for-each-ref",
        "--format=%(refname:short)%00%(upstream:short)",
        "refs/heads",
    )
    candidates: list[str] = []
    for line in result.stdout.splitlines():
        branch, _, upstream = line.partition("\x00")
        if (
            branch in {excluded_source_branch, repository.default_branch}
            or branch in worktrees
        ):
            continue
        if branch.startswith(("release/", "hotfix/")) or not upstream:
            continue
        if (
            git(
                repository.root,
                "show-ref",
                "--verify",
                f"refs/remotes/{upstream}",
                check=False,
            ).returncode
            == 0
        ):
            continue
        candidates.append(branch)
    return candidates


def clean_stale_branches(
    repository: RepositoryInfo,
    excluded_source_branch: str,
    progress: Progress,
    *,
    dry_run: bool = False,
) -> list[tuple[str, str]]:
    skipped: list[tuple[str, str]] = []
    for branch in stale_branch_candidates(repository, excluded_source_branch):
        try:
            oid = local_branch_oid(repository.root, branch)
            if oid is None:
                skipped.append((branch, "local branch disappeared"))
                continue
            if remote_branch_exists(repository, branch):
                skipped.append((branch, "remote branch exists"))
                continue
            payload = gh_json(
                repository.root,
                "pr",
                "list",
                "--state",
                "merged",
                "--head",
                branch,
                "--limit",
                "100",
                "--json",
                PR_FIELDS,
            )
        except CleanupError as error:
            skipped.append((branch, f"evidence query failed: {error}"))
            continue
        matches = [
            item
            for item in payload
            if item.get("headRefName") == branch
            and item.get("headRefOid") == oid
            and item.get("baseRefName") == repository.default_branch
            and not item.get("isCrossRepository")
        ]
        if len(matches) != 1:
            skipped.append(
                (branch, f"expected one matching merged PR; found {len(matches)}")
            )
            continue
        merge_oid = str((matches[0].get("mergeCommit") or {}).get("oid", ""))
        if (
            not merge_oid
            or git(
                repository.root,
                "merge-base",
                "--is-ancestor",
                merge_oid,
                repository.default_branch,
                check=False,
            ).returncode
            != 0
        ):
            skipped.append((branch, "merge commit is absent from the default branch"))
            continue
        assert progress.deleted_branches is not None
        if not dry_run:
            git(repository.root, "branch", "-D", branch)
        progress.deleted_branches.append((branch, oid))
    return skipped


def print_summary(
    repository: RepositoryInfo,
    pull_request: PullRequestInfo,
    progress: Progress,
    skipped_caches: list[tuple[Path, str]],
    stale_skipped: list[tuple[str, str]],
    dry_run: bool,
) -> None:
    prefix = "Dry run" if dry_run else "Cleanup complete"
    default_oid = (
        local_branch_oid(repository.root, repository.default_branch) or "not-local"
    )
    print(f"{prefix}: PR #{pull_request.number} ({pull_request.url})")
    print(f"Default branch: {repository.default_branch} @ {default_oid}")
    print(f"Caches: {progress.cache_files} files, {progress.cache_bytes} bytes")
    for branch, oid in progress.deleted_branches or []:
        action = "Would delete branch" if dry_run else "Deleted branch"
        print(f"{action}: {branch} @ {oid}")
        if not dry_run:
            print(f"Recover with: git branch {branch} {oid}")
    if not progress.deleted_branches:
        print(f"Source branch already absent: {pull_request.source_branch}")
    if skipped_caches or stale_skipped:
        print(
            f"Skipped: {len(skipped_caches)} cache targets, "
            f"{len(stale_skipped)} stale branches"
        )
        for path, reason in skipped_caches:
            print(f"  {path}: {reason}")
        for branch, reason in stale_skipped:
            print(f"  {branch}: {reason}")


def partial_completion(progress: Progress, pull_request: PullRequestInfo) -> str:
    completed: list[str] = []
    if progress.fetched:
        completed.append("remote refs fetched and pruned")
    if progress.cache_files or progress.cache_bytes:
        completed.append(
            f"{progress.cache_files} cache files ({progress.cache_bytes} bytes) deleted"
        )
    if progress.default_updated:
        completed.append("default branch updated")
    deleted = dict(progress.deleted_branches or [])
    for branch, oid in deleted.items():
        completed.append(f"local branch {branch} deleted at {oid}")
    if pull_request.source_branch not in deleted:
        completed.append(f"source branch retained at {pull_request.source_oid}")
    return "Partial completion: " + "; ".join(completed) + "."


def execute(args: argparse.Namespace) -> int:
    ensure_tools()
    root = repository_root()
    require_clean_worktree(root)
    repository = load_repository_info(root, args.remote)
    pull_request = load_pull_request(repository, args.pr)
    start = current_branch(root)
    source_oid = verify_source_identity(
        repository, pull_request, start, args.pr is not None
    )
    verify_worktree_ownership(repository, pull_request)
    if remote_branch_exists(repository, pull_request.source_branch):
        raise SafetyError("The remote source branch still exists.")
    caches, skipped_caches = build_cache_plan(root)
    progress = Progress()

    if args.dry_run:
        progress.cache_files = sum(target.file_count for target in caches)
        progress.cache_bytes = sum(target.byte_count for target in caches)
        if source_oid is not None:
            assert progress.deleted_branches is not None
            progress.deleted_branches.append((pull_request.source_branch, source_oid))
        stale_skipped = (
            clean_stale_branches(
                repository,
                pull_request.source_branch,
                progress,
                dry_run=True,
            )
            if args.all_stale
            else []
        )
        print_summary(
            repository,
            pull_request,
            progress,
            skipped_caches,
            stale_skipped,
            True,
        )
        return 0

    try:
        git(root, "fetch", repository.remote, "--prune")
        progress.fetched = True
        refreshed = load_pull_request(repository, pull_request.number)
        if refreshed != pull_request:
            raise SafetyError("Pull request identity changed after fetch.")
        verify_source_identity(repository, pull_request, start, True)
        verify_worktree_ownership(repository, pull_request)
        if remote_branch_exists(repository, pull_request.source_branch):
            raise SafetyError("The remote source branch was recreated.")
        verify_default_can_update(repository)
        caches, skipped_caches = build_cache_plan(root)
        delete_cache_plan(caches, progress)
        update_default_branch(repository, progress)
        verify_merge_present(repository, pull_request)

        refreshed = load_pull_request(repository, pull_request.number)
        if refreshed != pull_request or remote_branch_exists(
            repository, pull_request.source_branch
        ):
            raise SafetyError(
                "Remote pull request or branch state changed before deletion."
            )
        verify_worktree_ownership(repository, pull_request)
        delete_local_branch(repository, pull_request, progress)
        stale_skipped = (
            clean_stale_branches(repository, pull_request.source_branch, progress)
            if args.all_stale
            else []
        )
    except CleanupError as error:
        raise type(error)(
            f"{error}\n{partial_completion(progress, pull_request)}"
        ) from error
    except KeyboardInterrupt as error:
        raise InterruptedCleanup(partial_completion(progress, pull_request)) from error
    print_summary(
        repository,
        pull_request,
        progress,
        skipped_caches,
        stale_skipped,
        False,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        return execute(parse_args(argv))
    except CleanupError as error:
        print(f"cleanup-merged-branch: {error}", file=sys.stderr)
        return error.exit_code
    except KeyboardInterrupt:
        print(
            "cleanup-merged-branch: interrupted; rerun to resume safely.",
            file=sys.stderr,
        )
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
