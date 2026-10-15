---
name: verify-acceptance-items
description: Verify a GitHub pull request or GitLab merge request (MR) against the acceptance criteria (验收标准, task-list checkboxes) of the issues it links to, then tick the criteria it proves once the user confirms. Use when asked whether a PR or MR is done or meets its issue, when asked to check off an issue's checkboxes, or right after opening or updating a PR or MR that closes an issue.
---

# Verify Acceptance Items

## Vocabulary

- **Request**: a GitHub pull request or a GitLab merge request. Report it with
  the platform's own term, such as PR #12 or MR !42; `links` returns it as
  `request_label`.
- **Acceptance criterion**: a task-list item in an issue body that states
  something the work must satisfy. Its section heading varies: `Acceptance
  criteria`, `验收标准`, `Definition of done`, a bold line, a `<details>` block.
- **Item id**: the `L<line>` id `extract` gives every task-list item. Ids carry
  each criterion from `extract` through `brief` and the verdicts to `apply`.
- **Verdict**: `satisfied`, `unsatisfied`, or `undecidable`.

The script does every deterministic part: detecting the platform, resolving
links, parsing the body, rendering the judging brief, and ticking. You supply
the two judgements it cannot make: which items are acceptance criteria, and
what the user decides. Resolve the skill directory from the loaded `SKILL.md`
path and run
`uv run --script <skill-dir>/scripts/verify_acceptance_items.py <command>`.

The script detects the platform from the current branch's upstream remote, or
`origin`, and uses `gh` for GitHub and `glab` for GitLab. Exit code 2 with
"cannot tell whether ... is GitHub or GitLab" means the host is ambiguous: ask
the user which it is and pass `--platform github` or `--platform gitlab` on
every command. Exit code 2 naming `auth login` means the CLI is missing or
signed out: tell the user to run the command it names. Every command accepts
`--repo` (a project path such as `group/sub/project`, or a project URL) and
`--host`.

## 1. Resolve the issues

```console
uv run --script <skill-dir>/scripts/verify_acceptance_items.py links [--request <number>]
```

Without `--request` it uses the current branch's request. Each candidate
carries a `provenance`. Use a `closing_reference` directly. For a
`body_mention`, `related_issue`, or `branch_name` candidate, name it to the user
and get agreement before using it. Mention a `rejected` number only if the user
expected that issue to be covered.

Process every accepted candidate, each in its own report section, passing its
`repo` to the later commands.

## 2. Extract the structure

```console
uv run --script <skill-dir>/scripts/verify_acceptance_items.py extract --repo <project> --issue <number>
```

Each item comes with `id`, `text` (wrapped lines folded in), `state`
(`unchecked`, `checked`, or `inapplicable`), `depth`, `parent`, `sub_issue`, and
its `heading_path`. Keep the `body_sha256` for step 6.

Report every `skipped` entry as detected and not handled: `table` for
checkboxes in a table, `comment` for task lists in the issue's comments.

## 3. Choose the acceptance criteria

Pick sections from `heading_path` and the wording of their items. When more than
one section plausibly holds acceptance criteria, include them all and say why
each was included; over-listing costs the user a glance, under-listing hides a
criterion for good. Leave out `sub_issue: true` items, which only reference
another issue, and `inapplicable` items, which the author marked `[~]`; list
the latter in the report as inapplicable. If the body has checkboxes but none
read like acceptance criteria, say so plainly.

Done when every item is chosen, or excluded with a reason.

## 4. Judge in one subagent

```console
uv run --script <skill-dir>/scripts/verify_acceptance_items.py brief --repo <request-project> --request <number> --items "#42:L13,L15" "group/sub/spec#3:L4"
```

One `--items` selection per issue; the project prefix defaults to `--repo`. The
output is the complete prompt: the evidence commands for the platform, the
verdict definitions, the answer format, and the chosen criteria. Hand it
verbatim to a single subagent with a fresh context, and add nothing. A session
that wrote the request knows the intent behind the code, and that intent reads
as evidence the diff does not hold; one sentence of your own summary carries it
across.

One subagent covers every issue: the fresh context provides the isolation, and
reading the request dominates the cost. Split the selections across subagents
only when one brief is too large to hold, and say so in the report.

Report verdicts as returned. Where you disagree because of something outside
the request, carry the criterion as `undecidable` and let the user settle it.
Re-run `brief` and the subagent only to fix a faulty selection.

Without subagent support, follow the brief yourself and state that the judging
was not isolated.

## 5. Report, then wait

1. A table of every criterion with its id, verdict, and evidence.
2. A **numbered** queue of `undecidable` criteria, so the user can answer "1 and
   3 are satisfied".
3. Warnings for criteria already ticked that this request's evidence does not
   support.

Then stop. Write nothing until the user responds.

## 6. Tick

Tick `satisfied` criteria and `undecidable` ones the user adjudicated as
satisfied; mark the latter as user-adjudicated in the report. One call per
issue:

```console
uv run --script <skill-dir>/scripts/verify_acceptance_items.py apply --repo <project> --issue <number> --body-sha256 <from extract> --tick L13 L15
```

`apply` re-reads the body, aborts with exit code 2 if its hash changed, refuses
any id that is not a tickable item with exit code 64, flips only `[ ]`
characters, and reads the body back after writing. On exit code 2, re-run from
step 2 for that issue and ask the user again if its criteria changed. Exit code
2 after a permission failure means the account cannot write to that project;
the report still stands. Exit code 1 with a read-back difference means the
platform stored something other than what was sent: show the user the
difference and do not retry.

Post no comment unless the user asks; then comment on the request, not the
issue.

## Guardrails

- Every write goes through `apply`. A body rebuilt any other way loses images,
  HTML comments, and trailing whitespace the author may not be able to recover.
- Ticks only move from `[ ]` to `[x]`. A tick may rest on verification you
  cannot see, so an unsupported tick earns a warning and stays.
- `unsatisfied` criteria stay unticked.
