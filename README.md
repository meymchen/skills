# Agent skills

Reusable agent skills, grouped by domain under [`skills/`](skills/).

## Skills

- [`cleanup-merged-branch`](skills/routine/cleanup-merged-branch/README.md) —
  clean verified merged branches, reproducible caches, and stale local branches.
- [`verify-acceptance-items`](skills/development/verify-acceptance-items/README.md) —
  check a pull request against the acceptance criteria of its issue and tick the
  proven ones.

## Install

```console
npx skills@latest add meymchen/skills
```

## Plugins

This repository is also structured as a self-hosted plugin marketplace for
Claude Code and Codex. Available plugins:

- [`git-workflows`](plugins/git-workflows/README.md) commits changes, publishes
  pull requests, and cleans gone Git branches.
- [`engineering`](plugins/engineering/README.md) adapts Anthropic's ten engineering
  skills for personal and team projects, with shared Claude Code and Codex instructions.

See [Plugin marketplaces](docs/plugins.md) to create, validate, and publish a
plugin.

## License

[MIT](LICENSE), except for the adapted [Engineering plugin](plugins/engineering/README.md#license),
which retains its upstream license.
