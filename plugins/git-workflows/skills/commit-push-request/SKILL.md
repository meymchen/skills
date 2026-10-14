---
name: commit-push-request
description: Commit current changes, push the branch, and open a GitHub pull request or GitLab merge request (MR).
---

# Commit, Push, and Open a Request

A request is a GitHub pull request or a GitLab merge request. Report it with
the platform's own term, such as PR #12 or MR !42.

Inspect the changes:

```console
git status --short --branch
git diff HEAD
git branch --show-current
```

## Detect the platform

Find the remote: the current branch's upstream remote, or `origin` when the
branch has none.

```console
git config --get branch.<branch>.remote
git remote get-url <remote>
```

Take the host from the remote URL:

- `github.com` is GitHub; use `gh`.
- `gitlab.com`, or any host containing `gitlab`, is GitLab; use `glab`.
- For any other host, run `gh auth status --hostname <host>` and
  `glab auth status --hostname <host>`. The CLI that is authenticated decides
  the platform. Ask the user when neither or both are.

Confirm the chosen CLI is installed and authenticated for the host with the
same `auth status` command. If it is not, stop and tell the user to run
`gh auth login --hostname <host>` or `glab auth login --hostname <host>`. Do
not fall back to another tool or API.

## Commit, push, and open

1. Create a feature branch with `git switch -c <branch>` when currently on the
   default or a protected branch.
2. Stage the intended files and create one commit with an appropriate message.
3. Push with `git push --set-upstream <remote> HEAD`.
4. Derive the title and description from the inspected diff and create the
   request:

   ```console
   gh pr create --title <title> --body-file <file>
   glab mr create --title <title> --description-file <file> --yes
   ```

   Add `--base` or `--target-branch` only when the request should target a
   branch other than the default. Add options such as draft, squash, or
   source-branch removal only when the user asks for them.
5. Report the returned URL.

Do not stage unrelated changes or secrets, and do not force-push.
