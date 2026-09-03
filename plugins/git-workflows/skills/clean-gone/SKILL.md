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

`git branch -d` refuses a squash-merged branch, because the squashed commit on the
base branch is not an ancestor of the branch tip. Do not treat that refusal as
unmerged work on its own — test whether the branch's tree already landed on the
base branch:

```console
base=$(git rev-parse --abbrev-ref origin/HEAD)   # falls back to origin/main
merge_base=$(git merge-base "$base" "$branch")
squashed=$(git commit-tree "$branch^{tree}" -p "$merge_base" -m _)
git cherry "$base" "$squashed"
```

A leading `-` means the base branch already contains an equivalent patch: the
branch was squash- or rebase-merged, so delete it with `git branch -D <branch>`
and count it as a normal removal without asking. A leading `+` means real
unmerged work: keep the branch and report it.

Preserve the current, default, protected, dirty, or locked branches, and any
branch the check marks `+`. Report what was removed, which removals needed `-D`
after a `-` result, and what Git refused to remove. Beyond the `-` case, use
force only when the user explicitly requests it for named targets.
