---
name: cleanup-merged-branch
description: Safely clean a merged local branch, reproducible worktree caches, and stale local branches before updating the repository default branch.
disable-model-invocation: true
---

# Cleanup Merged Branch

Run this manual-only workflow only when the current user message explicitly invokes
`$cleanup-merged-branch` or `/cleanup-merged-branch`. Otherwise, stop without running
the script.

1. Resolve this skill directory from the loaded `SKILL.md` path.
2. From the target Git repository, map the user's explicit request to these optional
   script arguments:
   - PR number → `--pr NUMBER`
   - remote name → `--remote NAME`
   - all stale local branches → `--all-stale`
   - preview only → `--dry-run`
3. Run:

   ```console
   uv run --script <skill-dir>/scripts/cleanup_merged_branch.py [arguments]
   ```

4. Return the script's summary verbatim, followed only by concise context that the
   user needs to fix a reported blocker.

Treat the invocation as authorization for the script's verified local cleanup. Let
the script stop on ambiguous or unsafe state. Keep its safety gates intact, make no
GitHub writes, and install no missing tools.

Completion means the script returns zero and reports the default branch commit,
cache totals, and every deleted or already-absent local source branch.
