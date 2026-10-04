---
name: verify-acceptance-items
description: Verify a pull request against the acceptance criteria (验收标准, task-list checkboxes) of the GitHub issues it links to, then tick the criteria it proves once the user confirms. Use when asked whether a PR is done or meets its issue, when asked to check off an issue's checkboxes, or right after opening or updating a PR that closes an issue.
---

# Verify Acceptance Items

## Vocabulary

- **Acceptance criterion**: a task-list item in an issue body that states
  something the work must satisfy. Its section heading varies: `Acceptance
  criteria`, `验收标准`, `Definition of done`, a bold line, a `<details>` block.
- **Item id**: the `L<line>` id `extract` gives every task-list item. Ids carry
  each criterion from `extract` through `brief` and the verdicts to `apply`.
- **Verdict**: `satisfied`, `unsatisfied`, or `undecidable`.

The script does every deterministic part: resolving links, parsing the body,
rendering the judging brief, and ticking. You supply the two judgements it
cannot make: which items are acceptance criteria, and what the user decides.
Resolve the skill directory from the loaded `SKILL.md` path and run
`uv run --script <skill-dir>/scripts/verify_acceptance_items.py <command>`.

## 1. Resolve the issues

```console
uv run --script <skill-dir>/scripts/verify_acceptance_items.py links [--pr <number>]
```

Without `--pr` it uses the current branch's PR. Each candidate carries a
`provenance`. Use a `closing_reference` directly. For a `body_mention` or
`branch_name` candidate, name it to the user and get agreement before using it.
Mention a `rejected` number only if the user expected that issue to be covered.

Process every accepted candidate, each in its own report section, passing its
`repo` to the later commands.

## 2. Extract the structure

```console
uv run --script <skill-dir>/scripts/verify_acceptance_items.py extract --repo <owner/name> --issue <number>
```

Each item comes with `id`, `text` (wrapped lines folded in), `checked`, `depth`,
`parent`, `sub_issue`, and its `heading_path`. Keep the `body_sha256` for step 6.

Report every `skipped` entry as detected and not handled: `table` for
checkboxes in a table, `comment` for task lists in the issue's comments.

## 3. Choose the acceptance criteria

Pick sections from `heading_path` and the wording of their items. When more than
one section plausibly holds acceptance criteria, include them all and say why
each was included; over-listing costs the user a glance, under-listing hides a
criterion for good. Leave out `sub_issue: true` items, which are GitHub's own
tracking state. If the body has checkboxes but none read like acceptance
criteria, say so plainly.

Done when every item is chosen, or excluded with a reason.

## 4. Judge in one subagent

```console
uv run --script <skill-dir>/scripts/verify_acceptance_items.py brief --repo <pr-owner/name> --pr <number> --items "#42:L13,L15" "other/spec#3:L4"
```

One `--items` selection per issue; the repo prefix defaults to `--repo`. The
output is the complete prompt: the evidence rules, the verdict definitions, the
answer format, and the chosen criteria. Hand it verbatim to a single subagent
with a fresh context, and add nothing. A session that wrote the PR knows the
intent behind the code, and that intent reads as evidence the diff does not
hold; one sentence of your own summary carries it across.

One subagent covers every issue: the fresh context provides the isolation, and
reading the PR dominates the cost. Split the selections across subagents only
when one brief is too large to hold, and say so in the report.

Report verdicts as returned. Where you disagree because of something outside
the PR, carry the criterion as `undecidable` and let the user settle it. Re-run
`brief` and the subagent only to fix a faulty selection.

Without subagent support, follow the brief yourself and state that the judging
was not isolated.

## 5. Report, then wait

1. A table of every criterion with its id, verdict, and evidence.
2. A **numbered** queue of `undecidable` criteria, so the user can answer "1 and
   3 are satisfied".
3. Warnings for criteria already ticked that this PR's evidence does not
   support.

Then stop. Write nothing until the user responds.

## 6. Tick

Tick `satisfied` criteria and `undecidable` ones the user adjudicated as
satisfied; mark the latter as user-adjudicated in the report. One call per
issue:

```console
uv run --script <skill-dir>/scripts/verify_acceptance_items.py apply --repo <owner/name> --issue <number> --body-sha256 <from extract> --tick L13 L15
```

`apply` re-reads the body, aborts with exit code 2 if its hash changed, refuses
any id that is not a tickable item with exit code 64, and flips only `[ ]`
characters. On exit code 2, re-run from step 2 for that issue and ask the user
again if its criteria changed. Exit code 2 after a permission failure means the
account cannot write to that repo; the report still stands.

Post no comment unless the user asks; then comment on the PR, not the issue.

## Guardrails

- Every write goes through `apply`. A body rebuilt any other way loses images,
  HTML comments, and trailing whitespace the author may not be able to recover.
- Ticks only move from `[ ]` to `[x]`. A tick may rest on verification you
  cannot see, so an unsupported tick earns a warning and stays.
- `unsatisfied` criteria stay unticked.
