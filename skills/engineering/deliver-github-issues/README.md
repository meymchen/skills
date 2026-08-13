# Deliver GitHub issues

`deliver-github-issues` processes ready GitHub issues in dependency order with
a resumable Python state machine. It runs repository gates, audits acceptance
criteria, and squash-merges the exact tested commit.

- [Skill instructions](SKILL.md)
- [Usage guide](../../../docs/engineering/deliver-github-issues.md)

## Requirements

- Python 3.12 or newer
- uv 0.12.0 or newer
- Git and an authenticated GitHub CLI
- A configured Codex or Claude CLI

## Install locally

For editable local development on Windows, create junctions in the user-level
skill directories:

```powershell
New-Item -ItemType Junction `
  -Path "$HOME\.agents\skills\deliver-github-issues" `
  -Target (Resolve-Path ".\skills\engineering\deliver-github-issues").Path
New-Item -ItemType Junction `
  -Path "$HOME\.claude\skills\deliver-github-issues" `
  -Target (Resolve-Path ".\skills\engineering\deliver-github-issues").Path
```

## Run

Copy [`assets/repository.example.json`](assets/repository.example.json) to
`.github/deliver-github-issues.json` in the target repository and replace its
placeholder commands and CI checks. Then run from the target repository:

```console
uv run --project <skill-dir> --locked deliver-github-issues --issues "#14, #15-18"
```

The [usage guide](../../../docs/engineering/deliver-github-issues.md) covers
queue files, previews, recovery, acceptance gates, and exit codes.

## Develop

Run checks from this directory:

```console
uv run --locked ruff format --check .
uv run --locked ruff check .
uv run --locked pytest
```

`uv run` recreates `.venv` when needed. Virtual environments, build output,
test caches, and Python bytecode are ignored by Git and can be deleted safely.
