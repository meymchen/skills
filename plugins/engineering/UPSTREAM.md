# Upstream source and adaptation

- Source: [anthropics/knowledge-work-plugins, engineering](https://github.com/anthropics/knowledge-work-plugins/tree/8444efcd48f7012f09797778a36a33e73d0861f4/engineering)
- Revision: `8444efcd48f7012f09797778a36a33e73d0861f4`
- Upstream plugin version: `1.2.0`
- Local plugin version: `0.1.0`, versioned independently of upstream
- Upstream author: Anthropic
- Adaptation: Yuming Chen, 2026-10-04

## Changes

All ten upstream skills are retained with their names, main workflows, and
output templates. Modified Markdown files carry a dated adaptation notice.

- Add Codex metadata and a separate native manifest; adapt the Claude manifest
  to this repository's identity and distribution conventions.
- Replace Claude-specific argument placeholders and unqualified command
  examples with shared input instructions and host-specific invocation examples.
- Link every skill to the shared host, personal-use, and connector guidance.
- Explicitly declare six skills as user-or-model and four as user-only in both
  hosts; shorten the user-only descriptions and scope technical-debt matching.
  The [README](README.md#invocation-policy) records the classification and rationale.
- Make team roles, approvals, meetings, paging, and notifications conditional
  on the project's actual participants and process.
- Retain optional connector categories without distributing the upstream MCP
  configuration or depending on globally installed skills.
- Correct the README's skill names and remove unsupported settings and
  cross-session-memory claims.
- Apply repository Markdown formatting without changing the workflow structure.

The upstream debug workflow still proposes a fix and regression tests. This
adaptation does not add an autonomous execution framework, new skills, or hooks.

## License provenance

The upstream [LICENSE](https://github.com/anthropics/knowledge-work-plugins/blob/8444efcd48f7012f09797778a36a33e73d0861f4/LICENSE)
contains the Apache License 2.0 text and additional trailing text. The local
[LICENSE](LICENSE) preserves the entire file verbatim, including that trailing
text. No upstream NOTICE file was present at the pinned revision. The repository's
MIT license does not replace this plugin's upstream license.

The source and local license files have the same SHA-256:
`a6946284993aeec75c7d75906064b66210c46051e562077efb30eb6dcfe72e26`.

## Verification scope

Validated on Windows on 2026-10-04:

| Check | Result |
| --- | --- |
| Repository plugin and marketplace validation | Passed |
| Invocation policy metadata | Parsed all ten YAML pairs; six allow model selection, four require explicit invocation, and all retain user invocation |
| Python unit tests | 21 passed, including Apache-2.0 validation and publication |
| Ruff and repository Markdown checks | Passed |
| Claude Code 2.1.289 | Strict plugin/catalog validation, isolated local installation, and inventory of all ten skills passed |
| Codex CLI 0.160.0 | Isolated local discovery and installation passed; app-server `skills/list` loaded all ten skills, enabled and without errors; default prompts match their qualified names |
| Upstream license preservation | SHA-256 matches the pinned source |

Native checks used temporary configuration directories, without changing the
user's existing plugin installations or starting a model task. The generic
skill-creator validator does not recognize the explicit Claude invocation fields
or the retained `argument-hint` field. Use the native host validators for these
extensions; the generic validator is not a cross-host schema authority.

These checks establish local packaging, discovery, and loading. They do not
establish identical model outputs, successful execution of every workflow,
desktop UI behavior, remote GitHub installation, or behavior on another OS.
