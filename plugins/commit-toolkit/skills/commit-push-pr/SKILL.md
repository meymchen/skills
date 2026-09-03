---
name: commit-push-pr
description: Commit current changes, push the branch, and open a GitHub pull request.
---

# Commit, Push, and Open a Pull Request

Inspect the changes:

```console
git status --short --branch
git diff HEAD
git branch --show-current
```

Then:

1. Create a feature branch with `git switch -c <branch>` when currently on the
   default or a protected branch.
2. Stage the intended files and create one commit with an appropriate message.
3. Push with `git push --set-upstream origin HEAD`.
4. Create the pull request with `gh pr create`, deriving its title and body from
   the inspected diff and reporting the returned URL.

Do not stage unrelated changes or secrets, and do not force-push.
