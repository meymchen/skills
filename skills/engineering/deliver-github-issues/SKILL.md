---
name: deliver-github-issues
description: Deliver or audit one GitHub issue supplied by the repository queue orchestrator.
disable-model-invocation: true
---

# Deliver GitHub Issues

Invoke this user-only skill as `$deliver-github-issues` in Codex CLI or
`/deliver-github-issues` in Claude Code. Run the bundled
`scripts/Invoke-IssueQueue.ps1` from the target repository while resolving the
script path relative to this skill. Client metadata disables automatic model
invocation in both hosts.

Obey the `phase` field in the prompt. PowerShell owns every Git and GitHub
write; return structured evidence only.

Start a queue from `-Issues '#14, #15-18'` or a queue JSON file. Quote selectors
in PowerShell because `#` begins a comment. Issue selectors
use GitHub `blockedBy` and `blocking` relationships for a stable topological
order and require every selected issue to be open with `ready-for-agent`.
PowerShell prepends `$implement` to each issue's skill list by default.
Run state lives in `.agent-runs/deliver-github-issues/<run-id>/`; successful
queues remove their run directory, while stopped queues retain it for `-Resume`.

Copy `assets/repository.example.json` to
`.github/deliver-github-issues.json` and replace its checks with the target
repository's real local and CI gates. Use `-Config` for another path. This
policy also owns the readiness label, branch prefix, timeout, and low-cost
metadata agent.

Set `primaryAgent.provider` to `codex` or `claude` for implementation and audit
workers. Both receive the same JSON Schemas and never recursively invoke this
manual-only skill.

## Implement

1. Read `AGENTS.md`, `CONTEXT.md`, relevant ADRs, and the complete issue input.
2. Invoke every additional `$skill-name` listed in the prompt. Freely invoke
   any other installed and enabled skill relevant to the issue. If an explicit
   skill cannot load, return `blocked` and name it in `blockers`.
3. Implement the issue in the current branch and run targeted tests. Keep Git,
   GitHub, `.agent-runs/`, and `.scratch/` unchanged.
4. Return only an object matching `scripts/implement.schema.json`. Include each
   command actually run and its exit code. Report this skill plus every
   additional skill actually used in `usedSkills`.

Completion means the requested implementation is present and every reported
targeted test has an observed exit code.

## Audit

1. Treat the supplied head SHA, issue snapshot, local-gate log, and CI checks as
   the entire evidence boundary. Keep the workspace read-only.
2. Return one criterion for every original issue checkbox, in original order,
   matching its exact text.
3. Use `satisfied` only with reproducible file, successful-command, or CI-URL
   evidence. Use `human_required` for judgment or approval. Use `unsatisfied`
   for a concrete implementation gap.
4. Return only an object matching `scripts/audit.schema.json`. Model assertions
   are conclusions, never evidence.

Completion means every original checkbox has exactly one classified result.

## Metadata agents

Set `metadataAgent.provider` to `deterministic`, `codex`, `opencode`, `copilot`,
or `kimi`, plus an optional low-cost model name. PowerShell runs non-Codex
agents in an isolated temporary directory and accepts only validated commit／PR
metadata. It performs every Git and GitHub write and uses deterministic
metadata when an enabled fallback is needed. Model selection applies to Codex,
OpenCode, and Kimi; Copilot uses its configured CLI model.
