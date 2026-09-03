---
name: commit
description: Create a Git commit from the current changes.
---

# Commit

Inspect the repository:

```console
git status --short --branch
git diff HEAD
git log --oneline -10
```

Based on these results, stage the intended files with `git add -- <paths>` and
create one commit whose message matches the repository's style. Keep unrelated
changes and secrets out of the commit. Do not push.
