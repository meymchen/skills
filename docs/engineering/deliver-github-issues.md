# Deliver GitHub issues

`deliver-github-issues` moves ready GitHub issues from implementation through
exact-SHA squash merge. Its Python state machine processes one issue at a time,
runs repository gates, audits acceptance checkboxes, and stops at the first
failure.

[Read the skill source](../../skills/engineering/deliver-github-issues/SKILL.md).

## Requirements

- Python 3.12 or newer;
- uv 0.12.0 or newer;
- Git and an authenticated GitHub CLI;
- the configured Codex or Claude worker CLI;
- a clean target repository on its configured base branch;
- squash merging enabled.

Copy `assets/repository.example.json` from the skill to
`.github/deliver-github-issues.json` in the target repository. Replace the
placeholder commands and CI check names with real gates. Local commands are an
executable plus an argument array; shell strings, pipes, and redirects are not
interpreted.

Set the skill directory once for the examples below:

```console
SKILL_DIR=/path/to/deliver-github-issues
```

On PowerShell, use `$SKILL_DIR = "C:\path\to\deliver-github-issues"` and keep
the remaining `uv` arguments unchanged.

## Select issues

Selectors accept individual issues, inclusive ranges, comma-separated mixes,
and optional `#` prefixes:

```console
uv run --project "$SKILL_DIR" --locked deliver-github-issues --issues "#19, #23-26"
```

The orchestrator removes duplicates and reads GitHub `blockedBy` and `blocking`
relationships. Dependencies sort before dependents; unrelated issues retain
input order. Cycles, truncated relationships, non-ready issues, and open
blockers outside the selection fail preflight. Only the configured primary
agent is required.

Use a queue file for extra skills or per-issue instructions:

```console
uv run --project "$SKILL_DIR" --locked deliver-github-issues --queue queue.json
```

Queue files preserve their explicit issue order. The orchestrator prepends
`implement` to each issue's skill list.

## Preview

`--what-if` reads configuration, Git state, and GitHub relationships but makes
no changes and creates no run directory:

```console
uv run --project "$SKILL_DIR" --locked deliver-github-issues --issues "#19-23" --what-if
```

## Lifecycle

For each issue, the orchestrator fast-forwards the base, creates a branch,
invokes the implementation worker, runs local gates, commits, creates or
updates a pull request, waits for required CI, audits acceptance checkboxes,
and squash-merges the exact tested SHA. It then removes only that issue branch
and fast-forwards the base before touching the next issue.

Only acceptance criteria backed by direct file, successful-command, or CI-URL
evidence are checked automatically. An `unsatisfied` result returns the run to
implementation. A `human_required` result waits until all original checkboxes
are checked and the current `gh` user comments `/accept <40-character SHA>`.

## Resume

Failures and Ctrl+C preserve prompts, raw agent output, structured results,
logs, and strict version-1 state beneath
`.agent-runs/deliver-github-issues/<run-id>/`:

```console
uv run --project "$SKILL_DIR" --locked deliver-github-issues \
  --resume 20260813T021340Z-dc418758 \
  --instruction "Fix the gaps listed in the acceptance audit."
```

Resume always uses the policy embedded in the run. It therefore rejects
`--config` and `--what-if`. Successful runs remove their run directory.

## Exit codes

| Code | Meaning |
| ---: | --- |
| 10 | Configuration or preflight failure |
| 20 | Implementation or local-check failure |
| 30 | CI failure, conflict, missing check, or timeout |
| 40 | Acceptance gap or human gate |
| 50 | GitHub or tested-SHA drift |
| 130 | User interruption; resumable state is preserved when possible |

The implementation is verified on Windows and Ubuntu with Python 3.12. Tests
use temporary repositories and fake external executables; they do not access
live GitHub or agent services.
