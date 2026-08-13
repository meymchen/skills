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

The orchestrator owns every Git and GitHub write. Workers obey the `phase` in
their prompt and return structured evidence only. Issue selectors use GitHub
dependency relationships for a stable topological order. Queue files retain
their explicit order. The orchestrator prepends `$implement` once to every
issue's skill list.

Repository policy lives at `.github/deliver-github-issues.json`. Copy
`assets/repository.example.json` there and replace its local and CI checks.
The policy selects Codex or Claude for implementation and audit, plus an
optional metadata provider.

Run state lives at `.agent-runs/deliver-github-issues/<run-id>/`. Successful
runs remove their directory. Failed, interrupted, and human-gated runs preserve
state for `--resume`.

## Worker contracts

During `implement`, read repository instructions and the complete issue input,
invoke every requested skill in order, edit the current branch, and run
targeted tests. Do not commit, push, modify `.agent-runs/`, or write GitHub.
Return only the object required by `implement.schema.json`, including every
skill used and every observed test exit code.

During `audit`, keep the workspace read-only. Classify every original checkbox
in order. Use `satisfied` only with reproducible file, successful-command, or
CI-URL evidence; use `human_required` for judgment; use `unsatisfied` for an
implementation gap. Return only the object required by `audit.schema.json`.

Metadata providers are `deterministic`, `codex`, `opencode`, `copilot`, and
`kimi`. Non-deterministic providers must return one complete JSON object.
Copilot uses its CLI-configured model, so its policy model must be empty.
