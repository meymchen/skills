# Engineering Plugin

<!-- Adapted by Yuming Chen on 2026-10-04. See UPSTREAM.md. -->

A small adaptation of [Anthropic's Engineering plugin](https://github.com/anthropics/knowledge-work-plugins/tree/8444efcd48f7012f09797778a36a33e73d0861f4/engineering)
for personal and team projects in Claude Code and Codex. It retains all ten
upstream skills and their main workflows and output templates: standups, code
review, architecture decisions, incident response, debugging, deployment
checklists, system design, technical debt, testing strategy, and documentation.

The two hosts share the same skill instructions. There are no dependencies on
globally installed skills, and no MCP servers are bundled.

## Installation

From a local checkout of this repository, run the commands for your host:

```console
claude plugin marketplace add .
claude plugin install engineering@meymchen-skills
```

```console
codex plugin marketplace add .
codex plugin add engineering@meymchen-skills
```

Start a new session after installation. See [Plugin marketplaces](../../docs/plugins.md)
for repository installation and development commands. The GitHub marketplace
will contain this plugin only after these changes are published to that repository.

## Skills

All ten skills can be invoked explicitly. Six can also be selected by the model
when their descriptions match the task; four require explicit invocation.

| Skill | Description | Invocation |
| --- | --- | --- |
| `standup` | Summarize recent work, next steps, and blockers | User only |
| `code-review` | Review security, performance, correctness, and maintainability | User or model |
| `debug` | Reproduce, isolate, diagnose, and propose a fix | User or model |
| `architecture` | Create or evaluate an ADR with options and trade-offs | User only |
| `incident-response` | Triage, communicate, track mitigation, and write a postmortem | User only |
| `deploy-checklist` | Check release readiness and define rollback triggers | User only |
| `system-design` | Design services, APIs, data models, and system boundaries | User or model |
| `tech-debt` | Categorize and prioritize technical debt | User or model |
| `testing-strategy` | Plan tests, coverage, and example cases | User or model |
| `documentation` | Write READMEs, API docs, runbooks, and onboarding guides | User or model |

Claude Code uses `/engineering:<skill-name>`. Codex uses the skill picker or
`$engineering:<skill-name>`; select the Engineering plugin's skill when names overlap with
another installation. The actual upstream skill names are `code-review` and
`incident-response`, rather than the `/review` and `/incident` aliases previously
shown in its README.

### Invocation policy

Model-selectable skills support an ongoing engineering task without starting a
separate reporting or operational process. Standups, release checks, and incident
workflows have a user-chosen starting point. Formal ADR work is also explicit;
`system-design` supplies design guidance during ordinary architecture discussion.
This classification is a choice for this plugin, not a host-mandated list.

Every skill declares both Claude Code fields explicitly. `user-invocable: true`
keeps all skills available to the user; `disable-model-invocation` controls model
selection. Codex declares the inverse through `policy.allow_implicit_invocation`
in each `agents/openai.yaml`:

| Mode | Claude `disable-model-invocation` | Codex `allow_implicit_invocation` |
| --- | --- | --- |
| User or model | `false` | `true` |
| User only | `true` | `false` |

For user-only skills, ordinary related wording does not activate the packaged
workflow; invoke it explicitly using the host's command or skill picker. These
settings control skill selection, not authorization for external actions.
See [Claude Code invocation controls](https://code.claude.com/docs/en/skills#control-who-invokes-a-skill)
and [Codex invocation policy](https://learn.chatgpt.com/docs/build-skills#optional-metadata).

## Example Workflows

### Morning Standup

```text
Claude Code: /engineering:standup
Codex: $engineering:standup
```

Use connected tools or local activity to summarize recent work. For a solo
project, this is a personal progress note; it does not require a meeting or sharing.

### Code Review

```text
Claude Code: /engineering:code-review https://github.com/org/repo/pull/123
Codex: $engineering:code-review https://github.com/org/repo/pull/123
```

Share a PR link, paste a diff, or identify files. Get the upstream structured
review covering security, performance, correctness, and maintainability.

### Debugging an Issue

```text
Claude Code: /engineering:debug Users are getting 500 errors on the checkout page
Codex: $engineering:debug Users are getting 500 errors on the checkout page
```

Work through reproduction, isolation, diagnosis, and a proposed fix. This
adaptation retains the upstream workflow rather than adding an implementation pipeline.

### Architecture Decision

```text
Claude Code: /engineering:architecture Should this service use a queue or direct API calls?
Codex: $engineering:architecture Should this service use a queue or direct API calls?
```

Produce an ADR with alternatives, trade-offs, consequences, and action items.

### Incident Response

```text
Claude Code: /engineering:incident-response new The payments service is returning 503s
Codex: $engineering:incident-response new The payments service is returning 503s
```

The `new`, `update`, and `postmortem` modes are preserved. A solo maintainer can
track severity, mitigation, and a timeline without assigning a separate incident
commander or setting up a war room. Communications remain drafts unless sending
them is explicitly requested.

### Pre-Deploy Check

```text
Claude Code: /engineering:deploy-checklist auth-service v2.3.0
Codex: $engineering:deploy-checklist auth-service v2.3.0
```

Adapt the upstream checklist to the project's release process. Apply team
approvals, staging, and notifications only where relevant.

## Standalone and Connected Use

Every workflow can use repository content and information you provide. Host
connections can add PR activity, tickets, monitoring, CI results, or existing
design documents. Missing connectors do not require installing a service.

See [CONNECTORS.md](CONNECTORS.md) for categories, available-tool discovery,
and the boundaries for external actions.

## Project Context

Use project instructions, existing documentation, and the current request for
personalization. This adaptation does not introduce a settings file or promise
cross-session memory. Save a reusable checklist or decision in project docs
when needed.

## Development and Verification

```console
uv run --script scripts/create_plugin.py check engineering
claude plugin validate --strict plugins/engineering
```

Run these commands from the repository root. See [UPSTREAM.md](UPSTREAM.md) for
the source revision, changes, and verification scope.

## License

This plugin retains the upstream Apache-2.0 license. [LICENSE](LICENSE) is copied
verbatim from the pinned upstream revision. See [UPSTREAM.md](UPSTREAM.md) for
attribution and modification notes.
