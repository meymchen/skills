# Verify acceptance items

`verify-acceptance-items` checks whether a pull request satisfies the acceptance
criteria of the issues it is linked to, and ticks the ones the PR proves. A helper
script does every part with a deterministic answer. The agent keeps the two
judgements a script cannot make: which task-list items are acceptance criteria,
and, in a subagent that sees only the PR, whether each one is satisfied.

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
Claude Code and OpenCode. The agent also reaches for it on its own when asked
whether a PR is done, when asked to check off an issue's checkboxes, or right
after opening a PR that closes an issue; the confirmation step protects the issue
either way.

## Subcommands

```console
uv run --script <skill-dir>/scripts/verify_acceptance_items.py links [--pr 123]
uv run --script <skill-dir>/scripts/verify_acceptance_items.py extract --repo owner/name --issue 42
uv run --script <skill-dir>/scripts/verify_acceptance_items.py brief --pr 123 --items "#42:L13,L15" "other/spec#3:L4"
uv run --script <skill-dir>/scripts/verify_acceptance_items.py apply --issue 42 --body-sha256 <hash> --tick L13 L15 [--dry-run]
```

`--repo` defaults to the current repository.

- `links` reports each candidate issue with a `provenance` of
  `closing_reference`, `body_mention`, or `branch_name`, and rejects any number
  that resolves to a pull request, since GitHub shares one number space between
  issues and PRs. Without `--pr` it uses the current branch's PR.
- `extract` reports the issue body's task-list structure, giving every item an
  `L<line>` id, and the `body_sha256` of the body it read. It never chooses an
  acceptance section.
- `brief` renders the judging subagent's complete prompt from the chosen item
  ids, one selection per issue. Item texts come from the issue itself, and their
  checked state is left out, because an existing tick is not evidence.
- `apply` ticks the given item ids in one issue.

Item ids are the only thing the agent passes between commands, so no criterion
text or line is ever retyped by the model.

## Isolated judging

The verdicts are produced by one subagent with a fresh context, covering every
acceptance criterion of every issue. A session that just wrote the PR carries the
intent behind the code, and that intent reads as evidence for criteria the diff
does not prove. The subagent's prompt is the output of `brief`, passed on
verbatim: the repository, the PR number, the evidence and verdict rules, and the
criteria texts. Because the script writes it, the main session has no place to
add its own account of the PR. The rules keep the evidence to the diff, the
checks, and the PR's own prose; the subagent neither checks out the branch nor
runs tests. The prompt's wording lives in
[`assets/judge-brief.md`](../../skills/development/verify-acceptance-items/assets/judge-brief.md).

The judging is not split per issue. Isolation comes from the fresh context rather
than from the number of subagents, and the cost of the run is dominated by reading
the PR, a read each additional subagent would repeat to judge the same diff
against a different slice of the criteria.

The main session reports the verdicts rather than revising them. A disagreement
grounded in something outside the PR is carried as `undecidable` and put to the
user, who can adjudicate it in the confirmation step; ticks made on that basis are
marked user-adjudicated rather than evidenced. An agent without subagent support
follows the brief itself and says in its report that the judging was not
isolated.

## Safety model

The only write is a single character flip per ticked line.

Before writing, `apply` re-reads the issue body and compares its SHA-256 against
the value `extract` reported. A mismatch aborts the run: somebody edited the issue
in the meantime, so the recorded line numbers can no longer be trusted. A match
means the body is byte-identical to the one `extract` numbered, so the ids alone
address the right lines. `apply` then parses the body the same way `extract` did
and refuses any id that is not a task-list item, which rules out prose, table
cells, fenced code, and sub-issue entries such as `- [ ] #123`. Ticking an item
that is already `[x]` is reported as already checked rather than treated as an
error, which makes reruns safe.

The body is otherwise rewritten byte for byte, including line endings, images,
HTML comments, and trailing whitespace. The script never regenerates prose. GitHub
may normalise line endings on its own when storing the result, so
`body_sha256_after` describes the text that was sent, not necessarily what GitHub
stores.

Three kinds of checkbox are deliberately out of scope and reported rather than
handled: sub-issue entries, checkboxes inside tables, and task lists in issue
comments. The first two are flagged on the items and in `skipped`; comments are
scanned in a second read and reported in `skipped` with a `reason` of `comment`,
which `--no-comments` turns off. A checkbox in a comment is as likely to be
somebody's scratch list as a requirement, so it is surfaced rather than ticked.
Items already ticked are never unticked; when the PR's evidence does not support
one, the skill warns and leaves it alone, because a tick may rest on manual
verification the diff cannot show.

## Exit codes

- `0`: the command completed;
- `1`: a `gh` or filesystem operation failed;
- `2`: a precondition was not satisfied, including a changed issue body and
  missing write access;
- `64`: the command line was invalid, including an item id that is not a
  tickable task-list item;
- `130`: the user interrupted the run.
