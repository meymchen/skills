---
name: verify-acceptance-items
description: Check whether a pull request satisfies the acceptance items of the issues it is linked to, then tick the ones the PR proves. Use when working on a PR whose issue has checkboxes.
---

# Verify Acceptance Items

## Vocabulary

- **Acceptance item** — a task-list item in an issue body that states something the
  work must satisfy. Sub-issue entries (`- [ ] #123`) are not acceptance items;
  they are GitHub's own tracking state and must never be touched.
- **Acceptance section** — the part of the issue body holding those items. Its
  heading is not fixed: `Acceptance criteria`, `验收标准`, `Definition of done`,
  a bold line, or a `<details>` block are all real.
- **Verdict** — what you conclude about one acceptance item: `satisfied`,
  `unsatisfied`, or `undecidable`.

The script never decides which items are acceptance items. It reports structure;
you supply judgement. Resolve the skill directory from the loaded `SKILL.md` path
and run the script with `uv run --script <skill-dir>/scripts/verify_acceptance_items.py`.

## 1. Resolve the issues

Determine the PR from the user's request, or from the current branch with
`gh pr view <branch> --json number`. Then:

```console
uv run --script <skill-dir>/scripts/verify_acceptance_items.py links --pr <number>
```

Each candidate carries a `provenance`. Use a `closing_reference` directly. For a
`body_mention` or `branch_name` candidate, name it to the user and get agreement
before spending effort on it. Candidates in `rejected` were dropped because the
number turned out to be a pull request or could not be read; mention them only if
the user expected that issue to be covered.

Process every accepted candidate, each in its own report section. A candidate may
live in another repository; pass its `repo` through to the later commands.

## 2. Extract the structure

```console
uv run --script <skill-dir>/scripts/verify_acceptance_items.py extract --repo <owner/name> --issue <number>
```

Every task-list item comes back with `line`, `raw`, `text`, `checked`, `depth`,
`sub_issue`, `parent`, and its enclosing `heading_path`. Two fields matter for
different reasons: judge from `text` (wrapped lines are folded in), and pass
`raw` back untouched when ticking (it anchors the write).

`skipped` lists checkboxes found inside tables. Report them to the user as
detected and not handled. Do the same for any task list you notice in the issue's
comments — read them for context if you like, but never tick them.

## 3. Choose the acceptance sections

Pick from `heading_path` and the wording of the items themselves. When more than
one section plausibly holds acceptance items, **include all of them**. Over-listing
costs the user a glance; under-listing means an acceptance item is silently never
mentioned again. State why each section was included so a wrong pick is obvious at
the confirmation step.

Exclude items with `sub_issue: true`. If the issue has checkboxes but none read
like acceptance items, say so plainly rather than reporting "no acceptance items".

## 4. Judge each item

Evidence comes from the PR diff, its checks, and its body and commit messages.
Do not check out the branch and do not run tests.

- `satisfied` — cite a `path:line` or a check name. No citation, no verdict.
- `unsatisfied` — word it as **"no evidence in this PR"**, never as "this was not
  done". An issue can be satisfied across several PRs, and asserting absence
  invites the user to delete work that already exists elsewhere.
- `undecidable` — anything the diff cannot settle: manual verification, review by
  another person, behaviour on a device you cannot see. Use this verdict freely.
  Guessing here is worse than admitting the limit.

Judge nested children independently. Treat a parent as `satisfied` only when every
child is.

## 5. Report, then wait

Produce three parts:

1. A table of every acceptance item with its verdict and evidence.
2. A **numbered** queue of `undecidable` items, so the user can answer
   "1 and 3 are satisfied".
3. A warning list of items already ticked that this PR's evidence does not
   support. These are warnings only.

Then stop. Write nothing until the user responds.

## 6. Apply

Tick items that are `satisfied`, plus any `undecidable` item the user adjudicated
as satisfied. Mark the latter in the report as user-adjudicated, not evidenced, so
the basis stays recoverable. Never tick an `unsatisfied` item.

Build a plan and pipe it in:

```console
echo '{"repo":"owner/name","issue":42,"body_sha256":"<from extract>","tick":[{"line":13,"raw":"- [ ] ..."}]}' \
  | uv run --script <skill-dir>/scripts/verify_acceptance_items.py apply
```

`apply` re-reads the body, aborts if its hash or the target line changed, and flips
only the `[ ]` characters. If it aborts with exit code 2, re-run `extract` and
rebuild the plan; do not work around it. Exit code 2 after a permission failure
means the account cannot write to that repository — the report still stands.

Post no comment unless the user asks. When they do, comment on the PR, not the
issue.

## Never

- Regenerate the issue body. Images, HTML comments, and trailing whitespace are
  destroyed that way, and the author may not be able to recover them.
- Untick an item. A tick may rest on manual verification you cannot see; removing
  it replaces someone's knowledge with your ignorance. Warn instead.
- Tick a sub-issue entry, a table checkbox, or a checkbox in a comment.
