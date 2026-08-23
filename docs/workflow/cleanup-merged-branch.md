# Cleanup a merged branch

`cleanup-merged-branch` finishes local work after GitHub has merged a pull request
and deleted its remote source branch. It performs no GitHub writes and never deletes
a remote branch.

[Read the skill source](../../skills/productivity/cleanup-merged-branch/SKILL.md).

## Prerequisites

- Git, GitHub CLI (`gh`), and `uv` 0.12.3 or newer are installed.
- `gh` is authenticated for the GitHub host used by the repository remote.
- The current worktree is clean and no merge or rebase is in progress.
- The PR is merged into the repository default branch.
- GitHub has already deleted the remote source branch.

The script is written portably for Windows, macOS, and Linux. Cross-platform support
is verified in Windows and Ubuntu CI; macOS remains POSIX-inferred until exercised in
macOS CI.

## Install

Install globally for each supported client:

```console
npx skills@latest add meymchen/skills --skill cleanup-merged-branch --agent codex --global
npx skills@latest add meymchen/skills --skill cleanup-merged-branch --agent claude-code --global
npx skills@latest add meymchen/skills --skill cleanup-merged-branch --agent opencode --global
```

Invoke it explicitly as `$cleanup-merged-branch` in Codex or
`/cleanup-merged-branch` in Claude Code and OpenCode. The invocation itself
authorizes the verified local cleanup; normal execution has no confirmation prompt.

After upgrading from a release where `deliver-github-issues` lived under the
`engineering` domain, remove the old installation and add it again so one canonical
copy remains:

```console
npx skills@latest remove deliver-github-issues --global --agent '*' --yes
npx skills@latest add meymchen/skills --skill deliver-github-issues --agent codex --global
npx skills@latest add meymchen/skills --skill deliver-github-issues --agent claude-code --global
npx skills@latest add meymchen/skills --skill deliver-github-issues --agent opencode --global
```

## Options

The skill normally runs the script without arguments from the local PR source
branch. Explicit requests can add:

```console
uv run --script <skill-dir>/scripts/cleanup_merged_branch.py --pr 123
uv run --script <skill-dir>/scripts/cleanup_merged_branch.py --remote upstream
uv run --script <skill-dir>/scripts/cleanup_merged_branch.py --all-stale
uv run --script <skill-dir>/scripts/cleanup_merged_branch.py --dry-run
```

Use `--pr` when rerunning from the default branch or after the local source branch is
already absent. `--all-stale` extends the run to other local branches only when each
branch has a unique merged PR whose recorded head commit equals the local tip.
`--dry-run` queries GitHub and the remote without fetching, switching, deleting, or
updating local refs.

## Safety model

Before deletion, the script verifies the repository and remote identity, GitHub
authentication, merged PR state, default target branch, local source tip, remote
source absence, worktree ownership, and fast-forwardability. It repeats the remote
and branch checks after `fetch --prune` and immediately before local branch deletion.

The fixed cache allowlist is:

- recursive `__pycache__` directories;
- root `.pytest_cache`, `.ruff_cache`, `.mypy_cache`, `.pyright`, `.tox`, `.nox`,
  `.coverage`, `.coverage.*`, and `htmlcov` targets.

A target is skipped unless every entry is ignored and untracked. Links, junctions,
nested Git repositories, path escapes, `.venv`, `node_modules`, `.agent-runs`,
`.scratch`, build outputs, dependency trees, and ordinary untracked files remain
untouched. The run stops if the safe plan exceeds 10,000 files or 2 GiB.

The execution order is cache deletion, default-branch switch, fast-forward-only
pull, merge-commit verification, and local source-branch deletion. If interrupted,
rerun the skill; completed steps are idempotent. The summary records each deleted
branch tip and a `git branch <name> <sha>` recovery command.

## Exit codes

- `0`: cleanup or dry run completed safely;
- `1`: Git, GitHub CLI, or filesystem operation failed;
- `2`: a safety precondition was not satisfied;
- `64`: command-line usage was invalid;
- `130`: the user interrupted the run.
