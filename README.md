# Agent Skills

Reusable agent skills managed as a single source tree. Skills are grouped by
domain under `skills/`, following the layout used by
[`mattpocock/skills`](https://github.com/mattpocock/skills).

## Skills

- [`deliver-github-issues`](docs/engineering/deliver-github-issues.md)
  ([source](skills/engineering/deliver-github-issues/SKILL.md)) — deliver ready GitHub
  issues in dependency order with a resumable PowerShell state machine.

## Install

For editable local development, link the skill directory into both user-level
skill locations:

```powershell
New-Item -ItemType Junction `
  -Path "$HOME\.agents\skills\deliver-github-issues" `
  -Target (Resolve-Path ".\skills\engineering\deliver-github-issues").Path
New-Item -ItemType Junction `
  -Path "$HOME\.claude\skills\deliver-github-issues" `
  -Target (Resolve-Path ".\skills\engineering\deliver-github-issues").Path
```

After publishing the repository, compatible agents can also install from
GitHub:

```text
npx skills@latest add meymchen/skills
```

The skill is manual-only in Codex CLI and Claude Code. Keep repository-specific
policy in each target repository at `.github/deliver-github-issues.json`.
