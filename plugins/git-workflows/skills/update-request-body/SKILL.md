---
name: update-request-body
description: Update the title and body of one or more GitHub pull requests or GitLab merge requests (MRs).
---

# Update Request Body

A request is a GitHub pull request or a GitLab merge request. Report it with
the platform's own term, such as PR #12 or MR !42.

## Detect the platform

When the user gives a request URL, take the host from it. Otherwise find the
remote: the current branch's upstream remote, or `origin` when the branch has
none.

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

## Update

Resolve the requested request, or infer it from the current branch. Inspect
its existing title, body, base, head, commits, and diff before editing:

```console
gh pr view [<number>] --json number,url,title,body,baseRefName,headRefName,commits
gh pr diff [<number>]
glab mr view [<id>] --output json
glab mr diff [<id>] --raw
```

The GitLab JSON names these fields `iid`, `web_url`, `title`, `description`,
`target_branch`, and `source_branch`. List its commits with
`git log <remote>/<target_branch>..<remote>/<source_branch>`.

Write the title and body from the net change between the request base and
head:

- explain why first and what changed second;
- include meaningful verification and relevant issue links;
- preserve existing images, including GitLab `/uploads/` links, and useful
  content;
- omit abandoned approaches, local absolute paths, confidential terms, and
  routine checks already covered by CI.

For stacked requests, describe only the change relative to that request's
base. Apply the result, then return the request URL:

```console
gh pr edit <number> --title <title> --body-file <file>
glab mr update <id> --title <title> --description-file <file> --yes
```
