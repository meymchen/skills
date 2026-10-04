# Judge acceptance criteria against pull request #$pr

Decide, for each acceptance criterion below, whether pull request #$pr in
`$repo` proves it.

## Evidence

Read the PR only through `gh`:

- `gh pr diff $pr --repo $repo`
- `gh pr checks $pr --repo $repo`
- `gh pr view $pr --repo $repo --json title,body,commits`

The evidence is what the PR shows. Checking out the branch and running tests
are outside this task.

## Verdicts

- `satisfied`: the PR proves the criterion. Cite a `path:line` from the diff or
  a check name. No citation, no `satisfied`.
- `unsatisfied`: write the evidence as "no evidence in this PR". The work may
  live in another PR, so absence here proves nothing about the issue.
- `undecidable`: the PR cannot settle it, such as manual verification, review
  by another person, or behaviour on a device. Prefer it to a guess.

Judge each nested criterion on its own. A parent is `satisfied` only when every
child is.

## Answer

One line per criterion, in the order given, and nothing else:

```text
<owner/name>#<issue> <id> | <verdict> | <evidence>
```

## Criteria

$criteria
