# Plugin marketplaces

This repository is a marketplace-ready source for Claude Code and Codex. The
marketplace catalogs are currently empty: there are no published plugins to
install, and remote GitHub installation has not yet been exercised end to end.
The existing standalone skills remain available through the `skills` CLI.

## Layout

```text
.agents/plugins/marketplace.json       Codex marketplace
.claude-plugin/marketplace.json        Claude Code marketplace
plugins/_template/                     Non-installable dual-host template
plugins/<plugin-name>/                 Draft or published plugin
scripts/create_plugin.py               Plugin lifecycle command
```

A generated plugin contains one Claude Code manifest, one Codex manifest, a
minimal skill, and a development README. The native manifests are authoritative;
the repository checks that their shared identity fields agree.

## Requirements

- Python 3.12 or later
- [uv](https://docs.astral.sh/uv/)

Native smoke testing additionally requires Claude Code and Codex. Repository
checks do not require either host CLI.

## Create a draft

Run the command from the repository root:

```console
uv run --script scripts/create_plugin.py create my-plugin --description "Handle a focused workflow."
```

Names are normalized to lowercase kebab-case and limited to 64 characters. A
description must be one non-empty line of at most 120 characters. The default
version is `0.1.0`; use `--display-name` or `--version` to override generated
metadata.

To create and publish in one operation, add `--publish`. A failed publication
leaves a valid draft and does not leave a one-sided marketplace entry.

## Validate

Validate one plugin:

```console
uv run --script scripts/create_plugin.py check my-plugin
```

Validate the template, every plugin, both catalogs, and publication invariants:

```console
uv run --script scripts/create_plugin.py check --all
```

The repository check is deterministic and offline. CI also renders a temporary
plugin and exercises the native host CLIs in an isolated user directory.

## Publish and update

Publish a reviewed draft to both marketplaces:

```console
uv run --script scripts/create_plugin.py publish my-plugin
```

After editing the version, description, or other native metadata, validate the
plugin and synchronize the mutable catalog fields:

```console
uv run --script scripts/create_plugin.py check my-plugin
uv run --script scripts/create_plugin.py sync my-plugin
```

`publish` refuses existing entries. `sync` requires matching entries in both
catalogs. Neither command creates commits, tags, releases, or remote changes.
Plugin removal remains a reviewed manual change.

## Add the marketplaces

The catalogs can be added now, but remain empty until the first plugin is
published:

```console
claude plugin marketplace add meymchen/skills
codex plugin marketplace add meymchen/skills
```

After a plugin is published, install it by name:

```console
claude plugin install <plugin-name>@meymchen-skills
codex plugin add <plugin-name>@meymchen-skills
```

Remote GitHub installation will be added to the automated end-to-end checks
when the repository contains its first published plugin. Until then, CI proves
the same discovery and installation flow against a generated local marketplace.
