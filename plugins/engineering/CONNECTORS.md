# Connectors

<!-- Adapted by Yuming Chen on 2026-10-04. See UPSTREAM.md. -->

## Host invocation and personal use

Use the request and attachments supplied by the host. Claude Code exposes these
skills as `/engineering:<skill-name>`. In Codex, select the Engineering plugin's
skill from the skill picker or invoke `$engineering:<skill-name>`. If another installed skill
has the same name, select the one from this plugin. The workflow is shared;
host-specific argument substitution and a separate Skill tool are not required.
The `architecture`, `standup`, `deploy-checklist`, and `incident-response` skills
require explicit user invocation. The other six also allow model selection.

Follow the current project's instructions and document conventions. For solo
projects, the maintainer fills the applicable roles. Skip team approvals,
meetings, paging, and communication steps when there is no corresponding team
or audience. Keep those steps when the project calls for them. Use the user's
language for responses and the project's language for saved documents.

## How tool references work

Plugin files use `~~category` as a placeholder for tools already available in
the host. For example, `~~source control` might mean GitHub, GitLab, or an
available repository CLI. These are category labels, not literal tool names.

This plugin does not bundle MCP servers or require a particular connector.
Discover the tools actually available in the session. If a category is absent,
use local repository content and user-provided information; ask only for
missing information needed for the task. Do not invent remote activity, test
results, or monitoring data.

Having a connector does not authorize external actions. Create or update
tickets, configure monitoring, or change remote state only within the user's
authorized task. Send messages, page responders, or create communication
channels only when explicitly requested; otherwise prepare drafts. Existing
authorization applies without repeated confirmation.

## Connectors for this plugin

| Category | Placeholder | Examples |
| --- | --- | --- |
| Chat | `~~chat` | Slack, Microsoft Teams |
| Source control | `~~source control` | GitHub, GitLab, Bitbucket |
| Project tracker | `~~project tracker` | Linear, Asana, Jira, Shortcut, ClickUp |
| Knowledge base | `~~knowledge base` | Notion, Confluence, Guru, Coda |
| Monitoring | `~~monitoring` | Datadog, New Relic, Grafana, Splunk |
| Incident management | `~~incident management` | PagerDuty, Opsgenie, Incident.io, FireHydrant |
| CI/CD | `~~CI/CD` | CircleCI, GitHub Actions, Jenkins, Buildkite |
