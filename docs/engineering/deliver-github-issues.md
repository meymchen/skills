# Deliver GitHub issues

## What it does

`deliver-github-issues` takes a set of ready GitHub issues from implementation
through squash merge. It creates one branch and pull request at a time, waits
for the repository's local and CI checks, audits every acceptance checkbox, and
stops as soon as a step fails.

PowerShell owns the queue and every Git or GitHub write. Codex CLI or Claude
Code handles the implementation and evidence audit for the current issue. The
agent cannot commit, push, open a pull request, edit an issue, or merge on its
own.

[Read the skill source](../../skills/engineering/deliver-github-issues/SKILL.md).

## When to reach for it

Invoke the skill when several issues are ready to build and their dependency
links should decide the order. It also works for one issue when you want the
same local checks, CI checks, acceptance audit, and merge rules.

The skill is manual-only:

```text
Codex CLI:  $deliver-github-issues #14
Claude Code: /deliver-github-issues #14
```

It will not run because an agent happened to notice a `ready-for-agent` issue.
Another skill cannot start it either.

## Prerequisites

Run the skill from the target Git repository. The repository must have:

- an `origin` remote pointing to the repository named in the queue;
- a clean working tree on the configured base branch;
- squash merging enabled;
- `gh` installed and authenticated;
- the configured primary-agent CLI installed for implementation and audit;
- every selected issue open and carrying the configured readiness label;
- repository-specific policy at `.github/deliver-github-issues.json`.

Copy the bundled example before the first run, then replace its placeholder
check with the commands used by the target repository:

```powershell
New-Item -ItemType Directory -Path .github -Force | Out-Null
Copy-Item `
  "$HOME\.agents\skills\deliver-github-issues\assets\repository.example.json" `
  ".github\deliver-github-issues.json"
```

The policy chooses the readiness label, branch prefix, timeout, local gates,
required CI checks, primary agent, and metadata agent. `primaryAgent.provider`
accepts `codex` or `claude`.

## Select issues

Pass one issue, a range, or a comma-separated mix. Quote the selector in
PowerShell because `#` starts a comment there.

```powershell
$skill = "$HOME\.agents\skills\deliver-github-issues"
pwsh "$skill\scripts\Invoke-IssueQueue.ps1" `
  -Issues '#19, #23-26'
```

The selector removes duplicate numbers and reads GitHub's `blockedBy` and
`blocking` relationships. It then performs a stable topological sort: dependency
edges take priority, while unrelated issues keep the order in which you wrote
them. A cycle stops before implementation. An open blocker outside the selected
set also stops the run instead of silently skipping the dependency.

Every selected issue must be open and ready. The default implementation skill
is `implement`. The direct `-Issues` selector currently also checks for Codex
CLI while it resolves GitHub dependencies. A queue file does not have that
extra requirement, so a Claude-only setup should use `-Queue`.

Use a queue file when you need per-issue instructions or extra skills:

```json
{
  "version": 1,
  "repository": "owner/repository",
  "baseBranch": "main",
  "issues": [
    {
      "number": 19,
      "skills": ["tdd"],
      "instruction": "Keep the public API compatible."
    }
  ]
}
```

```powershell
$skill = "$HOME\.agents\skills\deliver-github-issues"
pwsh "$skill\scripts\Invoke-IssueQueue.ps1" `
  -Queue .\queue.json
```

Queue files preserve their explicit issue order. `implement` is prepended to
each issue's skill list, so it does not need to appear in the JSON. The worker
may use any other installed and enabled skill that helps with the issue, and it
must report every skill it actually used.

## Preview a run

Add `-WhatIf` to validate the queue or selector and print the full operation
sequence without creating branches, run state, commits, pull requests, or issue
updates.

```powershell
$skill = "$HOME\.agents\skills\deliver-github-issues"
pwsh "$skill\scripts\Invoke-IssueQueue.ps1" `
  -Issues '#19, #23-26' `
  -WhatIf
```

## What one issue does

The queue processes one issue through the entire lifecycle before touching the
next one:

1. Validate repository state, tools, authentication, labels, and merge policy.
2. Fast-forward the base branch and create the configured issue branch.
3. Give the issue, comments, acceptance criteria, and repository instructions
   to the primary agent.
4. Run every configured local check independently of the agent.
5. Commit the verified diff, push it, and create a pull request.
6. Wait until every required CI check appears and passes on the tested SHA.
7. Audit each original acceptance checkbox against files, command results, or
   CI URLs.
8. Squash merge the exact tested SHA, close the issue, delete only the issue
   branch, and fast-forward the local base branch.

No change, a failed command, a missing check, a changed pull request head, or a
concurrent issue edit stops the queue. The next issue receives no branch, agent,
or GitHub call.

## Acceptance and human gates

The audit classifies every original checkbox as `satisfied`, `unsatisfied`, or
`human_required`.

Only `satisfied` criteria are checked automatically. `unsatisfied` returns the
run to implementation on resume. A criterion marked `human_required` leaves
the branch and pull request open until the operator:

1. checks every remaining issue checkbox; and
2. comments `/accept <40-character-head-sha>` while logged in as the same `gh`
   user who resumes the run.

The SHA must match exactly. Any new commit invalidates the audit, CI evidence,
and human approval.

## Stop and resume

Stopped runs remain under
`.agent-runs/deliver-github-issues/<run-id>/` in the target repository. The
directory contains state, prompts, structured results, and command logs.

Resume only after addressing the reported failure:

```powershell
$skill = "$HOME\.agents\skills\deliver-github-issues"
pwsh "$skill\scripts\Invoke-IssueQueue.ps1" `
  -Resume 20260813T021340Z-dc418758 `
  -Instruction 'Fix the gaps listed in the acceptance audit.'
```

There is no automatic retry. A successful queue removes its run directory;
failed and gated queues retain theirs.

## Metadata agents

Commit titles, pull request titles, and summaries can come from
`deterministic`, `codex`, `opencode`, `copilot`, or `kimi`. This is the only
part of the workflow intended for a lower-cost model. PowerShell validates the
returned metadata and still performs the commit and pull request creation.

Set `metadataAgent.fallback` to `true` to use deterministic metadata when the
configured CLI is unavailable or returns invalid output. Copilot uses the model
configured in its CLI; leave `metadataAgent.model` empty for that provider.

## Exit codes

Code | Meaning
--- | ---
10 | Configuration or preflight failure
20 | Implementation or local-check failure
30 | CI failure, conflict, missing check, or timeout
40 | Acceptance gap or human gate
50 | GitHub or tested-SHA drift

## Large queues

Queues with more than 20 issues do not require one context window large enough
to hold the whole batch. Each implementation and audit call receives only the
current issue and its evidence. The PowerShell state file carries progress
between calls.

Large queues still take time because they are deliberately serial. A queue of
20 issues can involve 40 primary-agent calls plus local checks, CI waits, and
merges. Use `-WhatIf` first, and split the queue when you want a shorter recovery
unit.

## It's working if

- the preview shows the dependency order you expect;
- only one issue branch and pull request are active at a time;
- the pull request head matches the SHA recorded in the run state;
- issue checkboxes are checked only when the comment includes direct evidence;
- a failed issue leaves a resumable run and the following issue untouched;
- a successful queue ends on a clean, fast-forwarded base branch.
