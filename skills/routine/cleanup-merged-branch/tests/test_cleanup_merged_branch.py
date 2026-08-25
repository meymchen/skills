from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILL_ROOT = Path(__file__).parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "cleanup_merged_branch.py"


def run_git(repository: Path, *args: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=repository,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(
            f"git {' '.join(args)} failed\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )
    return result


def write_fake_gh(directory: Path, state: dict[str, object]) -> tuple[Path, Path]:
    state_path = directory / "gh-state.json"
    log_path = directory / "gh-calls.jsonl"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    fake = directory / "fake-gh.py"
    fake.write_text(
        """\
import json
import os
import sys
from pathlib import Path

state = json.loads(Path(os.environ["FAKE_GH_STATE"]).read_text(encoding="utf-8"))
log_path = Path(os.environ["FAKE_GH_LOG"])
previous = log_path.read_text(encoding="utf-8").splitlines() if log_path.exists() else []
with log_path.open("a", encoding="utf-8") as stream:
    stream.write(json.dumps(sys.argv[1:]) + "\\n")

args = sys.argv[1:]
if args[:2] == ["auth", "status"]:
    raise SystemExit(0)
if args[:2] == ["repo", "view"]:
    print(json.dumps(state["repo"]))
    raise SystemExit(0)
if args[:2] == ["pr", "view"]:
    views = state.get("pr_views", [state["pr"]])
    view_count = sum(json.loads(line)[:2] == ["pr", "view"] for line in previous)
    print(json.dumps(views[min(view_count, len(views) - 1)]))
    raise SystemExit(0)
if args[:2] == ["pr", "list"]:
    print(json.dumps(state.get("prs", [])))
    raise SystemExit(0)
print(f"unsupported fake gh command: {args}", file=sys.stderr)
raise SystemExit(2)
""",
        encoding="utf-8",
    )
    return fake, log_path


def create_merged_repository(directory: Path) -> tuple[Path, str, str]:
    remote = directory / "remote.git"
    repository = directory / "work"
    remote.mkdir()
    repository.mkdir()
    run_git(remote, "init", "--bare", "--initial-branch=main")
    run_git(repository, "init", "--initial-branch=main")
    run_git(repository, "config", "user.name", "Test User")
    run_git(repository, "config", "user.email", "test@example.com")
    run_git(repository, "config", "protocol.file.allow", "always")
    (repository / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")
    (repository / "app.txt").write_text("base\n", encoding="utf-8")
    run_git(repository, "add", ".gitignore", "app.txt")
    run_git(repository, "commit", "-m", "initial")
    remote_url = "https://github.com/owner/repository.git"
    run_git(repository, "config", f"url.{remote.as_uri()}.insteadOf", remote_url)
    run_git(repository, "remote", "add", "origin", remote_url)
    run_git(repository, "push", "-u", "origin", "main")

    run_git(repository, "switch", "-c", "feat/3-cleanup")
    (repository / "app.txt").write_text("feature\n", encoding="utf-8")
    run_git(repository, "add", "app.txt")
    run_git(repository, "commit", "-m", "feature")
    source_sha = run_git(repository, "rev-parse", "HEAD").stdout.strip()
    run_git(repository, "push", "-u", "origin", "feat/3-cleanup")

    run_git(repository, "switch", "main")
    run_git(repository, "merge", "--squash", "feat/3-cleanup")
    run_git(repository, "commit", "-m", "feat: squash merge")
    merge_sha = run_git(repository, "rev-parse", "HEAD").stdout.strip()
    run_git(repository, "push", "origin", "main")
    run_git(repository, "push", "origin", "--delete", "feat/3-cleanup")
    run_git(repository, "switch", "feat/3-cleanup")

    cache = repository / "package" / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "module.pyc").write_bytes(b"cache")
    pytest_cache = repository / ".pytest_cache"
    pytest_cache.mkdir()
    (pytest_cache / "state").write_text("cache", encoding="utf-8")
    return repository, source_sha, merge_sha


def github_state(
    source_sha: str,
    merge_sha: str,
    **overrides: object,
) -> dict[str, object]:
    pull_request: dict[str, object] = {
        "number": 3,
        "state": "MERGED",
        "headRefName": "feat/3-cleanup",
        "headRefOid": source_sha,
        "baseRefName": "main",
        "mergeCommit": {"oid": merge_sha},
        "isCrossRepository": False,
        "url": "https://github.com/owner/repository/pull/3",
    }
    pull_request.update(overrides)
    return {
        "repo": {
            "nameWithOwner": "owner/repository",
            "defaultBranchRef": {"name": "main"},
        },
        "pr": pull_request,
    }


def invoke_cleanup(
    directory: Path,
    repository: Path,
    state: dict[str, object],
    *arguments: str,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    fake_gh, gh_log = write_fake_gh(directory, state)
    env = os.environ.copy()
    env["CLEANUP_MERGED_BRANCH_GH"] = str(fake_gh)
    env["FAKE_GH_STATE"] = str(directory / "gh-state.json")
    env["FAKE_GH_LOG"] = str(gh_log)
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        cwd=repository,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    return result, gh_log


def create_directory_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
        if result.returncode != 0:
            raise OSError(result.stderr or result.stdout)
    else:
        link.symlink_to(target, target_is_directory=True)


class CleanupMergedBranchCliTests(unittest.TestCase):
    def test_skill_assets_follow_repository_conventions(self) -> None:
        repository_root = SKILL_ROOT.parents[2]
        skill = (SKILL_ROOT / "SKILL.md").read_text(encoding="utf-8")
        metadata = (SKILL_ROOT / "agents" / "openai.yaml").read_text(encoding="utf-8")
        script = SCRIPT.read_text(encoding="utf-8")
        documentation = (
            repository_root / "docs" / "routine" / "cleanup-merged-branch.md"
        ).read_text(encoding="utf-8")
        workflow = (
            repository_root / ".github" / "workflows" / "cleanup-merged-branch-python.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("disable-model-invocation: true", skill)
        skill_body = skill.split("---", 2)[2]
        self.assertNotIn("$cleanup-merged-branch", skill_body)
        self.assertNotIn("/cleanup-merged-branch", skill_body)
        self.assertIn("allow_implicit_invocation: false", metadata)
        self.assertIn('# requires-python = ">=3.12"', script)
        self.assertIn("# dependencies = []", script)
        self.assertIn("uv run --script", documentation)
        self.assertEqual(list((SKILL_ROOT / "scripts").glob("*.py")), [SCRIPT])
        self.assertIn("ubuntu-latest", workflow)
        self.assertIn("windows-latest", workflow)

    def test_help_exposes_the_supported_public_options(self) -> None:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--pr", result.stdout)
        self.assertIn("--remote", result.stdout)
        self.assertIn("--all-stale", result.stdout)
        self.assertIn("--dry-run", result.stdout)

    def test_cleans_a_verified_squash_merged_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, source_sha, merge_sha = create_merged_repository(root)
            fake_gh, gh_log = write_fake_gh(
                root,
                {
                    "repo": {
                        "nameWithOwner": "owner/repository",
                        "defaultBranchRef": {"name": "main"},
                    },
                    "pr": {
                        "number": 3,
                        "state": "MERGED",
                        "headRefName": "feat/3-cleanup",
                        "headRefOid": source_sha,
                        "baseRefName": "main",
                        "mergeCommit": {"oid": merge_sha},
                        "isCrossRepository": False,
                        "url": "https://github.com/owner/repository/pull/3",
                    },
                },
            )
            env = os.environ.copy()
            env["CLEANUP_MERGED_BRANCH_GH"] = str(fake_gh)
            env["FAKE_GH_STATE"] = str(root / "gh-state.json")
            env["FAKE_GH_LOG"] = str(gh_log)

            result = subprocess.run(
                [sys.executable, str(SCRIPT)],
                cwd=repository,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(run_git(repository, "branch", "--show-current").stdout.strip(), "main")
            branches = run_git(repository, "branch", "--format=%(refname:short)").stdout
            self.assertNotIn("feat/3-cleanup", branches.splitlines())
            self.assertFalse((repository / "package" / "__pycache__").exists())
            self.assertFalse((repository / ".pytest_cache").exists())
            calls = [json.loads(line) for line in gh_log.read_text(encoding="utf-8").splitlines()]
            self.assertTrue(all(call[0] in {"auth", "repo", "pr"} for call in calls))
            self.assertNotIn("edit", {argument for call in calls for argument in call})

    def test_dry_run_reports_the_plan_without_mutating_the_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, source_sha, merge_sha = create_merged_repository(root)
            fake_gh, gh_log = write_fake_gh(
                root,
                {
                    "repo": {
                        "nameWithOwner": "owner/repository",
                        "defaultBranchRef": {"name": "main"},
                    },
                    "pr": {
                        "number": 3,
                        "state": "MERGED",
                        "headRefName": "feat/3-cleanup",
                        "headRefOid": source_sha,
                        "baseRefName": "main",
                        "mergeCommit": {"oid": merge_sha},
                        "isCrossRepository": False,
                        "url": "https://github.com/owner/repository/pull/3",
                    },
                },
            )
            env = os.environ.copy()
            env["CLEANUP_MERGED_BRANCH_GH"] = str(fake_gh)
            env["FAKE_GH_STATE"] = str(root / "gh-state.json")
            env["FAKE_GH_LOG"] = str(gh_log)

            result = subprocess.run(
                [sys.executable, str(SCRIPT), "--dry-run"],
                cwd=repository,
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"Would delete branch: feat/3-cleanup @ {source_sha}", result.stdout)
            self.assertEqual(
                run_git(repository, "branch", "--show-current").stdout.strip(),
                "feat/3-cleanup",
            )
            self.assertTrue((repository / "package" / "__pycache__").exists())
            self.assertTrue((repository / ".pytest_cache").exists())
            branches = run_git(repository, "branch", "--format=%(refname:short)").stdout
            self.assertIn("feat/3-cleanup", branches.splitlines())

    def test_refuses_unverified_or_changed_source_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, source_sha, merge_sha = create_merged_repository(root)

            result, _ = invoke_cleanup(
                root,
                repository,
                github_state(source_sha, merge_sha, state="OPEN"),
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("not merged", result.stderr)

            run_git(repository, "push", "origin", "feat/3-cleanup")
            result, _ = invoke_cleanup(root, repository, github_state(source_sha, merge_sha))
            self.assertEqual(result.returncode, 2)
            self.assertIn("remote source branch still exists", result.stderr)
            run_git(repository, "push", "origin", "--delete", "feat/3-cleanup")

            result, _ = invoke_cleanup(
                root,
                repository,
                github_state("0" * 40, merge_sha),
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("differs from PR head", result.stderr)

            (repository / "notes.txt").write_text("uncommitted\n", encoding="utf-8")
            result, _ = invoke_cleanup(root, repository, github_state(source_sha, merge_sha))
            self.assertEqual(result.returncode, 2)
            self.assertIn("uncommitted or untracked", result.stderr)

            branches = run_git(repository, "branch", "--format=%(refname:short)").stdout
            self.assertIn("feat/3-cleanup", branches.splitlines())
            self.assertEqual(
                run_git(repository, "branch", "--show-current").stdout.strip(),
                "feat/3-cleanup",
            )

    def test_refuses_closed_cross_repository_or_non_default_prs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, source_sha, merge_sha = create_merged_repository(root)
            cases = (
                ({"state": "CLOSED"}, "not merged"),
                ({"isCrossRepository": True}, "Cross-repository"),
                ({"baseRefName": "release"}, "is not default branch"),
            )
            for overrides, message in cases:
                with self.subTest(overrides=overrides):
                    result, _ = invoke_cleanup(
                        root,
                        repository,
                        github_state(source_sha, merge_sha, **overrides),
                    )
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(message, result.stderr)

    def test_refuses_an_in_progress_rebase(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, source_sha, merge_sha = create_merged_repository(root)
            git_dir = Path(run_git(repository, "rev-parse", "--absolute-git-dir").stdout.strip())
            (git_dir / "REBASE_HEAD").write_text(source_sha + "\n", encoding="utf-8")

            result, _ = invoke_cleanup(root, repository, github_state(source_sha, merge_sha))

            self.assertEqual(result.returncode, 2)
            self.assertIn("merge or rebase is in progress", result.stderr)

    def test_refuses_a_non_fast_forward_default_or_another_worktree_owner(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, source_sha, merge_sha = create_merged_repository(root)
            run_git(repository, "switch", "main")
            (repository / "local-only.txt").write_text("local\n", encoding="utf-8")
            run_git(repository, "add", "local-only.txt")
            run_git(repository, "commit", "-m", "local default divergence")
            run_git(repository, "switch", "feat/3-cleanup")

            result, _ = invoke_cleanup(root, repository, github_state(source_sha, merge_sha))

            self.assertEqual(result.returncode, 2)
            self.assertIn("cannot be fast-forwarded", result.stderr)
            self.assertTrue((repository / ".pytest_cache").exists())

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, source_sha, merge_sha = create_merged_repository(root)
            other_worktree = root / "default-worktree"
            run_git(repository, "worktree", "add", str(other_worktree), "main")

            result, _ = invoke_cleanup(root, repository, github_state(source_sha, merge_sha))

            self.assertEqual(result.returncode, 2)
            self.assertIn("checked out in another worktree", result.stderr)
            self.assertTrue((repository / ".pytest_cache").exists())

    def test_skips_a_cache_containing_a_nested_repository(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, source_sha, merge_sha = create_merged_repository(root)
            nested_git = repository / ".pytest_cache" / "fixture" / ".git"
            nested_git.mkdir(parents=True)
            (nested_git / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

            result, _ = invoke_cleanup(root, repository, github_state(source_sha, merge_sha))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("nested Git repository", result.stdout)
            self.assertTrue((repository / ".pytest_cache").exists())
            self.assertFalse((repository / "package" / "__pycache__").exists())

    def test_skips_a_cache_containing_a_tracked_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, _source_sha, merge_sha = create_merged_repository(root)
            tracked = repository / ".pytest_cache" / "tracked.txt"
            tracked.write_text("keep\n", encoding="utf-8")
            run_git(repository, "add", "--force", str(tracked.relative_to(repository)))
            run_git(repository, "commit", "-m", "track cache fixture")
            source_sha = run_git(repository, "rev-parse", "HEAD").stdout.strip()

            result, _ = invoke_cleanup(root, repository, github_state(source_sha, merge_sha))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("target contains tracked files", result.stdout)

    def test_skips_a_cache_link_that_escapes_the_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, source_sha, merge_sha = create_merged_repository(root)
            outside = root / "outside"
            outside.mkdir()
            sentinel = outside / "sentinel.txt"
            sentinel.write_text("preserve\n", encoding="utf-8")
            link = repository / ".pytest_cache" / "outside-link"
            try:
                create_directory_link(link, outside)
            except OSError as error:
                self.skipTest(f"directory links unavailable: {error}")

            result, _ = invoke_cleanup(root, repository, github_state(source_sha, merge_sha))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("symbolic link or junction", result.stdout)
            self.assertTrue(link.exists())
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve\n")

    def test_deletes_a_root_cache_only_once_when_it_contains_python_cache(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, _source_sha, merge_sha = create_merged_repository(root)
            with (repository / ".gitignore").open("a", encoding="utf-8") as stream:
                stream.write(".tox/\n")
            run_git(repository, "add", ".gitignore")
            run_git(repository, "commit", "-m", "ignore tox cache")
            source_sha = run_git(repository, "rev-parse", "HEAD").stdout.strip()
            nested = repository / ".tox" / "env" / "__pycache__"
            nested.mkdir(parents=True)
            (nested / "module.pyc").write_bytes(b"cache")

            result, _ = invoke_cleanup(root, repository, github_state(source_sha, merge_sha))

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((repository / ".tox").exists())

    def test_stops_when_the_cache_plan_exceeds_two_gibibytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, source_sha, merge_sha = create_merged_repository(root)
            huge = repository / ".pytest_cache" / "huge.bin"
            with huge.open("wb") as stream:
                stream.truncate(2 * 1024 * 1024 * 1024 + 1)

            result, _ = invoke_cleanup(root, repository, github_state(source_sha, merge_sha))

            self.assertEqual(result.returncode, 2)
            self.assertIn("exceeds hard limit", result.stderr)
            self.assertTrue(huge.exists())
            self.assertEqual(
                run_git(repository, "branch", "--show-current").stdout.strip(),
                "feat/3-cleanup",
            )

    def test_stops_when_the_cache_plan_exceeds_ten_thousand_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, source_sha, merge_sha = create_merged_repository(root)
            cache = repository / ".pytest_cache"
            for index in range(10_001):
                (cache / f"entry-{index}").touch()

            result, _ = invoke_cleanup(root, repository, github_state(source_sha, merge_sha))

            self.assertEqual(result.returncode, 2)
            self.assertIn("exceeds hard limit", result.stderr)
            self.assertTrue(cache.exists())
            branches = run_git(repository, "branch", "--format=%(refname:short)").stdout
            self.assertIn("feat/3-cleanup", branches.splitlines())

    def test_reruns_idempotently_from_default_with_an_explicit_pr(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, source_sha, merge_sha = create_merged_repository(root)
            state = github_state(source_sha, merge_sha)
            first, _ = invoke_cleanup(root, repository, state)
            self.assertEqual(first.returncode, 0, first.stderr)
            cache = repository / ".pytest_cache"
            cache.mkdir()
            (cache / "again").write_text("cache", encoding="utf-8")

            second, _ = invoke_cleanup(root, repository, state, "--pr", "3")

            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertIn("Source branch already absent", second.stdout)
            self.assertFalse(cache.exists())
            self.assertEqual(run_git(repository, "branch", "--show-current").stdout.strip(), "main")

    def test_all_stale_deletes_only_a_uniquely_verified_branch(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, source_sha, merge_sha = create_merged_repository(root)
            for branch in ("feat/old", "feat/ambiguous", "release/keep"):
                run_git(repository, "branch", branch, source_sha)
                run_git(repository, "config", f"branch.{branch}.remote", "origin")
                run_git(
                    repository,
                    "config",
                    f"branch.{branch}.merge",
                    f"refs/heads/{branch}",
                )
            exact = {
                "number": 2,
                "state": "MERGED",
                "headRefName": "feat/old",
                "headRefOid": source_sha,
                "baseRefName": "main",
                "mergeCommit": {"oid": merge_sha},
                "isCrossRepository": False,
                "url": "https://github.com/owner/repository/pull/2",
            }
            ambiguous = {
                **exact,
                "number": 1,
                "headRefName": "feat/ambiguous",
            }
            state = github_state(source_sha, merge_sha)
            state["prs"] = [exact, ambiguous, {**ambiguous, "number": 4}]

            result, _ = invoke_cleanup(root, repository, state, "--all-stale")

            self.assertEqual(result.returncode, 0, result.stderr)
            branches = run_git(repository, "branch", "--format=%(refname:short)").stdout
            self.assertNotIn("feat/old", branches.splitlines())
            self.assertIn("feat/ambiguous", branches.splitlines())
            self.assertIn("release/keep", branches.splitlines())

    def test_dry_run_previews_verified_stale_branches_without_deleting_them(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, source_sha, merge_sha = create_merged_repository(root)
            run_git(repository, "branch", "feat/old", source_sha)
            run_git(repository, "config", "branch.feat/old.remote", "origin")
            run_git(
                repository,
                "config",
                "branch.feat/old.merge",
                "refs/heads/feat/old",
            )
            exact = {
                "number": 2,
                "state": "MERGED",
                "headRefName": "feat/old",
                "headRefOid": source_sha,
                "baseRefName": "main",
                "mergeCommit": {"oid": merge_sha},
                "isCrossRepository": False,
                "url": "https://github.com/owner/repository/pull/2",
            }
            state = github_state(source_sha, merge_sha)
            state["prs"] = [exact]

            result, _ = invoke_cleanup(
                root,
                repository,
                state,
                "--dry-run",
                "--all-stale",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(f"Would delete branch: feat/old @ {source_sha}", result.stdout)
            branches = run_git(repository, "branch", "--format=%(refname:short)").stdout
            self.assertIn("feat/old", branches.splitlines())
            self.assertIn("feat/3-cleanup", branches.splitlines())

    def test_reports_partial_completion_when_remote_evidence_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, source_sha, merge_sha = create_merged_repository(root)
            state = github_state(source_sha, merge_sha)
            changed = {
                **state["pr"],
                "headRefOid": "0" * 40,
            }
            state["pr_views"] = [state["pr"], state["pr"], changed]

            result, _ = invoke_cleanup(root, repository, state)

            self.assertEqual(result.returncode, 2)
            self.assertIn("Partial completion", result.stderr)
            self.assertIn("default branch updated", result.stderr)
            self.assertIn("source branch retained", result.stderr)
            self.assertEqual(run_git(repository, "branch", "--show-current").stdout.strip(), "main")
            branches = run_git(repository, "branch", "--format=%(refname:short)").stdout
            self.assertIn("feat/3-cleanup", branches.splitlines())
            self.assertFalse((repository / ".pytest_cache").exists())


if __name__ == "__main__":
    unittest.main()
