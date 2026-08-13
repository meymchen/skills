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
- Codex CLI or Claude Code as the primary agent
- OpenCode 1.18.18+ or Kimi Code CLI 0.29.0+ as the metadata agent
- `implement`, `tdd`, and `code-review` installed from `mattpocock/skills`

## Install locally

Install the upstream implementation skills with `npx skills`, then share the
same user-level source with Claude Code. On Windows, the links can be junctions:

```powershell
New-Item -ItemType Junction `
  -Path "$HOME\.claude\skills\implement" `
  -Target (Resolve-Path "$HOME\.agents\skills\implement").Path
```

Create equivalent links for `tdd` and `code-review`. Install this repository's
skill separately with `npx skills@latest add meymchen/skills`.

## Run

Copy [`assets/repository.example.json`](assets/repository.example.json) to
`.github/deliver-github-issues.json` in the target repository and replace its
placeholder commands and CI checks. Then run from the target repository:

```console
uv run --project <skill-dir> --locked deliver-github-issues --issues "#14, #15-18"
```

The defaults are `--primary-agent codex --metadata-agent opencode`. Select
Claude Code or Kimi explicitly with `--primary-agent claude` and
`--metadata-agent kimi`.

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
