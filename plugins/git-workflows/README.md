# Git Workflows

Streamline routine Git work with focused skills adapted from Anthropic's
`commit-commands` plugin:

- `commit` creates a focused local commit.
- `commit-push-request` commits changes, pushes a branch, and opens a GitHub
  pull request or GitLab merge request.
- `update-request-body` updates a pull or merge request from its net change.
- `clean-gone` safely removes merged local branches whose upstreams are gone.

The workflows keep command usage small and task-focused while preserving
unrelated changes and requiring explicit scope for force operations.

## GitHub and GitLab

The request skills detect the platform from the branch's remote host and use
`gh` for GitHub or `glab` for GitLab, including GitHub Enterprise Server and
self-managed GitLab. Hosts other than `github.com` and `gitlab.com` are
resolved by the CLI authenticated for them. Install and log in to the matching
CLI first.

Version 0.2.0 renamed `commit-push-pr` to `commit-push-request` and
`update-pr-body` to `update-request-body`. The old names no longer exist.

## Install

```console
claude plugin install git-workflows@meymchen-skills
codex plugin add git-workflows@meymchen-skills
```

## Development

Validate it from the repository root:

```console
uv run --script scripts/create_plugin.py check git-workflows
```

After changing metadata, synchronize its entries in both Marketplace catalogs:

```console
uv run --script scripts/create_plugin.py sync git-workflows
```
