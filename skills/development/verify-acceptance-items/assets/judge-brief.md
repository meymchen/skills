# Judge acceptance criteria against $label

Decide, for each acceptance criterion below, whether $request proves it.

## Evidence

Read the $noun only through these commands:

$evidence

The evidence is what the $noun shows. Checking out the branch and running tests
are outside this task.

## Verdicts

- `satisfied`: the $noun proves the criterion. Cite a `path:line` from the diff
  or a $check. No citation, no `satisfied`.
- `unsatisfied`: write the evidence as "no evidence in this $noun". The work may
  live in another $noun, so absence here proves nothing about the issue.
- `undecidable`: the $noun cannot settle it, such as manual verification, review
  by another person, or behaviour on a device. Prefer it to a guess.

Judge each nested criterion on its own. A parent is `satisfied` only when every
child is.

## Answer

One line per criterion, in the order given, and nothing else:

```text
<project>#<issue> <id> | <verdict> | <evidence>
```

## Criteria

$criteria
