---
name: update-pr-body
description: Update the title and body of one or more GitHub pull requests.
---

# Update PR Body

Resolve the requested PR, or infer it from the current branch with `gh pr view`.
Inspect its existing title, body, base, head, commits, and diff before editing.

Write the title and body from the net change between the PR base and head:

- explain why first and what changed second;
- include meaningful verification and relevant issue links;
- preserve existing images and useful content;
- omit abandoned approaches, local absolute paths, confidential terms, and
  routine checks already covered by CI.

For stacked PRs, describe only the change relative to that PR's base. Apply the
result with `gh pr edit`, then return the PR URL.
