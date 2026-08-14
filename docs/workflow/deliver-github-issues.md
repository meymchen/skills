# Deliver GitHub issues

`deliver-github-issues` moves ready GitHub issues from implementation through
exact-SHA squash merge. Its Python state machine processes one issue at a time,
runs repository gates, audits acceptance checkboxes, and stops at the first
failure.

[Read the skill source](../../skills/workflow/deliver-github-issues/SKILL.md).

## Requirements

- Python 3.12 or newer;
- uv 0.12.0 or newer;
- Git and an authenticated GitHub CLI;
- the CLIs selected for either role: Codex CLI, Claude Code, OpenCode 1.18.18+,
  or Kimi Code CLI 0.29.0+;
- when Claude is selected as primary, a working Claude Code Bash sandbox
  (macOS, Linux, or WSL2); native Windows fails closed;
- `implement`, `tdd`, and `code-review` installed by `npx skills` under
  `.agents/skills` and linked into `.claude/skills` when Claude is selected;
- a clean target repository on its configured base branch;
- squash merging enabled.

Copy `assets/repository.example.json` from the skill to
`.github/deliver-github-issues.json` in the target repository. Replace the
placeholder commands, timeouts, retry limit, and CI check names with real gates. Local commands are an
executable plus an argument array; shell strings, pipes, and redirects are not
interpreted.

## Select issues

Selectors accept individual issues, inclusive ranges, comma-separated mixes,
and optional `#` prefixes:

```console
uv run --project <skill-dir> --locked deliver-github-issues --issues "#19, #23-26"
```

The orchestrator accepts only Open issues carrying the mapped `ready-for-agent`
label. It removes duplicates and reads GitHub `blockedBy` and `blocking`
relationships. Dependencies sort before dependents; unrelated issues retain
input order. Cycles, truncated relationships, non-ready issues, and open
blockers outside the selection fail preflight. To discover every eligible
Issue explicitly, use `--all-ready`; ordinary runs never expand their scope.
The selected Issue body is hashed at admission and must still match when its
implementation starts.

Use a queue file for extra skills or per-issue instructions:

```console
uv run --project <skill-dir> --locked deliver-github-issues --queue queue.json
```

Queue files use their explicit issue order as the tie-breaker after the same
dependency sort. The orchestrator prepends `implement` to each issue's skill
list.

## Select agents

Agent selection belongs to a new run, not repository policy:

```console
uv run --project <skill-dir> --locked deliver-github-issues --issues "#19-23" --primary-agent claude --metadata-agent kimi
```

Primary defaults to `codex`; metadata defaults to `opencode`. Both accept
`codex|claude|opencode|kimi` independently, producing 16 valid routes. All CLIs
use their own default model. The chosen providers and detected versions are
embedded in run state; resume rejects new agent flags. There is no runtime
auto-detection, fallback provider, or per-run model override.

The 16 routes are covered by automated routing tests. The native Windows smoke
matrix recorded on 2026-08-14 passed primary and metadata probes for Codex,
OpenCode, and Kimi. Claude primary was not verified because the CLI reported
that its required sandbox is unavailable on native Windows; Claude metadata was
not verified because the configured organization rejected API access with HTTP
403. These are explicit verification boundaries, not fallback conditions.

## Preview

`--what-if` reads configuration, Git state, and GitHub relationships but makes
no changes and creates no run directory:

```console
uv run --project <skill-dir> --locked deliver-github-issues --issues "#19-23" --what-if
```

## Lifecycle

For each issue, the orchestrator fast-forwards the base, creates a branch, and
invokes `$implement <full-issue-url>` in Codex, `/implement <full-issue-url>` in
Claude, or the corresponding shared `implement` skill in OpenCode and Kimi.
OpenCode discovers `.agents/skills`; Kimi receives that directory through
`--skills-dir`. The primary may create local provisional commits and must use
`code-review`. The orchestrator runs local gates, squashes all Issue work to one final commit, creates or
updates a pull request, waits for required CI, audits acceptance checkboxes,
and squash-merges the exact tested SHA. It then removes only that issue branch
and fast-forwards the base before touching the next issue.

The provisional commit receives a separate read-only `code-review` pass. Local
gates run once before metadata generation and again after the final squash, so
the recorded tested SHA is the commit that actually passed.
The worker handoff's commit and changed-file claims are checked against Git.
Protected refs, Issue timestamps, remote branches, and pull requests are
snapshotted around primary calls to detect forbidden side effects.
Claude receives a fail-closed OS sandbox with no outbound network and no
unsandboxed-command escape hatch. Codex uses its workspace-write sandbox.

Local, CI, or acceptance failures return to the same primary automatically.
The shared retry budget defaults to three and is configured by
`maxPrimaryFixAttempts`. Primary, metadata, and CI phases have separate policy
timeouts.
Recognized transient provider failures, such as rate limits or connection
resets, are retried once; timeouts are not retried because the worker may have
already changed the workspace.

Metadata runs once on the normal path after local verification. Every provider
runs in an isolated temporary working directory without project skills,
plugins, or delegation. Claude, OpenCode, and Kimi receive an empty or deny-all
tool set. Codex uses `read-only`, ignores user configuration and rules, disables
MCP servers, and is rejected if its event stream contains a tool call; Codex CLI
does not currently hide its registered built-in tools. Only verified summaries
and successful command names are passed in. A tool event or invalid JSON stops
the run without fallback or automatic repair.

Preview performs static availability, version, authentication, skill-source,
and role-specific isolation checks. A formal run additionally invokes the
selected primary and metadata protocols through read-only／no-side-effect
capability probes before creating an issue branch.

Only acceptance criteria backed by direct file, successful-command, or CI-URL
evidence are checked automatically. An `unsatisfied` result returns the run to
implementation. A `human_required` result waits until all original checkboxes
are checked and the current `gh` user comments `/accept <40-character SHA>`.

## Resume

Failures and Ctrl+C preserve prompts, raw agent output, structured results,
logs, and strict version-1 state beneath
`.agent-runs/deliver-github-issues/<run-id>/`:

```console
uv run --project <skill-dir> --locked deliver-github-issues --resume 20260813T021340Z-dc418758 --instruction "Fix the gaps listed in the acceptance audit."
```

Resume always uses the policy and agents embedded in the run. It therefore
rejects `--config`, `--what-if`, and agent flags. Successful runs remove their
active run directory and retain a compact summary with a 30-day expiry under
`.agent-runs/deliver-github-issues/summaries/`.

Pass `--keep-run-summary` on a new run to keep that summary permanently. Remove
expired summaries explicitly with:

```console
uv run --project <skill-dir> --locked deliver-github-issues --clean-summaries
```

## Exit codes

| Code | Meaning |
| ---: | --- |
| 10 | Configuration or preflight failure |
| 20 | Implementation or local-check failure |
| 30 | CI failure, conflict, missing check, or timeout |
| 40 | Acceptance gap or human gate |
| 50 | GitHub or tested-SHA drift |
| 130 | User interruption; resumable state is preserved when possible |

The implementation is written portably and has a Windows／Ubuntu Python 3.12
CI matrix. Tests use temporary repositories and fake external executables;
they do not access live GitHub or agent services. Call it cross-platform only
after both CI jobs pass for the exact commit under review.
