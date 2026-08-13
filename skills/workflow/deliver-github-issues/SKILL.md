---
name: deliver-github-issues
description: Deliver or audit GitHub issues through a resumable Python queue orchestrator.
disable-model-invocation: true
---

# Deliver GitHub Issues

Invoke this manual-only skill as `$deliver-github-issues` in Codex CLI or
`/deliver-github-issues` in Claude Code. From the target repository, run its
Python orchestrator with the skill directory as the uv project:

```console
uv run --project <skill-dir> --locked deliver-github-issues --issues "#14, #15-18"
```

The orchestrator owns every remote Git and GitHub write. Workers obey the
`phase` in their prompt and return structured evidence. Issue selectors use GitHub
dependency relationships for a stable topological order. Queue files retain
their explicit order only as a tie-breaker after dependency sorting. The orchestrator prepends `$implement` once to every
issue's skill list. Codex invokes it as `$implement <full-issue-url>`; Claude
Code invokes the shared personal skill as `/implement <full-issue-url>`.

Repository policy lives at `.github/deliver-github-issues.json`. Copy
`assets/repository.example.json` there and replace its local and CI checks.
Agent routing is selected per new run. The primary defaults to Codex CLI and
accepts `codex|claude`; metadata defaults to OpenCode and accepts
`opencode|kimi`. Resume always reuses the selection stored in run state.
Claude requires its fail-closed Bash sandbox, available on macOS, Linux, or
WSL2; native Windows is rejected rather than running unsandboxed.

Run state lives at `.agent-runs/deliver-github-issues/<run-id>/`. Successful
runs remove their active directory and retain a compact 30-day summary. Failed,
interrupted, and human-gated runs preserve full state for `--resume`.
`--keep-run-summary` keeps a successful summary permanently;
`--clean-summaries` removes expired summaries.

## Worker contracts

During `implement`, read repository instructions and the complete issue input,
invoke every requested skill in order, edit the current branch, and run
targeted tests. Local provisional commits are allowed. Do not push, modify
`.agent-runs/`, switch branches, or write GitHub.
Return only the object required by `implement.schema.json`, including every
skill used and every observed test exit code.

After the provisional commit, a separate read-only `code-review` invocation
reviews the complete diff. The final squashed commit is accepted only after the
repository gates pass again at that exact SHA.

During `audit`, keep the workspace read-only. Classify every original checkbox
in order. Use `satisfied` only with reproducible file, successful-command, or
CI-URL evidence; use `human_required` for judgment; use `unsatisfied` for an
implementation gap. Return only the object required by `audit.schema.json`.

Metadata providers run in a temporary directory with tools, delegation, project
configuration, plugins, and skills disabled. Their JSON event stream must end
in one schema-valid metadata object; any tool event fails the run. There is no
fallback provider and no per-run model selection.
