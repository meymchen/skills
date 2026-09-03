---
name: clean-gone
description: Remove local Git branches whose upstreams are gone, including associated worktrees.
---

# Clean Gone Branches

Identify stale branches and their worktrees:

```console
git fetch --prune origin
git branch -vv
git worktree list
```

For branches marked `[gone]`, remove associated worktrees with
`git worktree remove <path>`, then delete the branch with `git branch -d <branch>`.
Preserve the current, default, protected, dirty, locked, or unmerged branches.
Report what was removed and what Git refused to remove. Use force only when the
user explicitly requests it for named targets.
