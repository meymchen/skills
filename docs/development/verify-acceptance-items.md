# Verify acceptance items

`verify-acceptance-items` checks whether a pull request satisfies the acceptance
items of the issues it is linked to, and ticks the ones the PR proves. Judgement
stays with the agent; a helper script does the parts that must be deterministic.

[Read the skill source](../../skills/development/verify-acceptance-items/SKILL.md).

## Prerequisites

- GitHub CLI (`gh`) and `uv` 0.12.3 or newer are installed.
- `gh` is authenticated for the host serving the repository.
- Read access to the pull request and to every issue it references. Write access
  is needed only to tick; the report is produced without it.

The script is written portably for Windows, macOS, and Linux. Cross-platform
support is verified in Windows and Ubuntu CI; macOS remains POSIX-inferred until
exercised in macOS CI.

## Install

```console
npx skills@latest add meymchen/skills --skill verify-acceptance-items --agent codex --global
npx skills@latest add meymchen/skills --skill verify-acceptance-items --agent claude-code --global
npx skills@latest add meymchen/skills --skill verify-acceptance-items --agent opencode --global
```

Invoke it as `$verify-acceptance-items` in Codex or `/verify-acceptance-items` in
Claude Code and OpenCode. The agent may also reach for it on its own while working
on a PR; the confirmation step below is what protects the issue either way.

## Subcommands

```console
uv run --script <skill-dir>/scripts/verify_acceptance_items.py links --pr 123
uv run --script <skill-dir>/scripts/verify_acceptance_items.py extract --repo owner/name --issue 42
uv run --script <skill-dir>/scripts/verify_acceptance_items.py apply --dry-run
```

`--repo` defaults to the current repository. `links` reports each candidate issue
with a `provenance` of `closing_reference`, `body_mention`, or `branch_name`, and
rejects any number that resolves to a pull request, since GitHub shares one number
space between issues and PRs. `extract` reports the issue body's task-list
structure and never chooses an acceptance section. `apply` reads its plan from
stdin:

```json
{
  "repo": "owner/name",
  "issue": 42,
  "body_sha256": "<the value extract reported>",
  "tick": [{ "line": 13, "raw": "- [ ] the exact anchor line" }]
}
```

## Safety model

The only write is a single character flip per ticked line.

Before writing, `apply` re-reads the issue body, compares its SHA-256 against the
value `extract` reported, and compares each target line against the `raw` text
recorded for it. A mismatch on either check aborts the run: somebody edited the
issue in the meantime, so the recorded line numbers can no longer be trusted.
Ticking a line that is already `[x]` is reported as already checked rather than
treated as an error, which makes reruns safe.

The body is otherwise rewritten byte for byte, including line endings, images,
HTML comments, and trailing whitespace. The script never regenerates prose. GitHub
may normalise line endings on its own when storing the result, so
`body_sha256_after` describes the text that was sent, not necessarily what GitHub
stores.

Three kinds of checkbox are deliberately out of scope and reported rather than
handled: sub-issue entries such as `- [ ] #123`, checkboxes inside tables, and
task lists in issue comments. Items already ticked are never unticked; when the
PR's evidence does not support one, the skill warns and leaves it alone, because
a tick may rest on manual verification the diff cannot show.

## Exit codes

- `0`: the command completed;
- `1`: a `gh` or filesystem operation failed;
- `2`: a precondition was not satisfied, including a changed issue body, a target
  line that no longer matches, and missing write access;
- `64`: the command line or the stdin plan was invalid;
- `130`: the user interrupted the run.
