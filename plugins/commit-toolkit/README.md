# Commit Toolkit

Streamline routine Git work with three focused skills adapted from Anthropic's
`commit-commands` plugin:

- `commit` creates a focused local commit.
- `commit-push-pr` commits changes, pushes a branch, and opens a GitHub pull
  request.
- `clean-gone` safely removes merged local branches whose upstreams are gone.

The workflows keep command usage small and task-focused while preserving
unrelated changes and requiring explicit scope for force operations.

## Install

```console
claude plugin install commit-toolkit@meymchen-skills
codex plugin add commit-toolkit@meymchen-skills
```

## Development

Validate it from the repository root:

```console
uv run --script scripts/create_plugin.py check commit-toolkit
```

After changing metadata, synchronize its entries in both Marketplace catalogs:

```console
uv run --script scripts/create_plugin.py sync commit-toolkit
```
