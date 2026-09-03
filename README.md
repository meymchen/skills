# Agent skills

Reusable agent skills, grouped by domain under [`skills/`](skills/).

## Skills

- [`cleanup-merged-branch`](skills/routine/cleanup-merged-branch/README.md) —
  clean verified merged branches, reproducible caches, and stale local branches.
- [`update-pr-body`](skills/development/update-pr-body/SKILL.md) — update the title
  and body of one or more pull requests.
- [`verify-acceptance-items`](skills/development/verify-acceptance-items/README.md) —
  check a pull request against the acceptance items of its issue and tick the
  proven ones.

## Install

```console
npx skills@latest add meymchen/skills
```

## Plugins

This repository is also structured as a self-hosted plugin marketplace for
Claude Code and Codex. It currently provides marketplace infrastructure and a
dual-host plugin template, but no published plugins.

See [Plugin marketplaces](docs/plugins.md) to create, validate, and publish a
plugin.

## License

[MIT](LICENSE)
