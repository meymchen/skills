---
name: tech-debt
description: Identify, categorize, and prioritize technical debt when assessing maintenance backlog, refactoring priorities, or the cost and risk of deferred work. Keep the assessment within the requested project or component.
disable-model-invocation: false
user-invocable: true
---

<!-- Adapted by Yuming Chen on 2026-10-04 for personal use and dual-host support. See ../../UPSTREAM.md. -->

# Tech Debt Management

> Read [CONNECTORS.md](../../CONNECTORS.md) for host invocation, personal use, and optional tools.

Systematically identify, categorize, and prioritize technical debt.

## Categories

| Type | Examples | Risk |
| ------ | ---------- | ------ |
| **Code debt** | Duplicated logic, poor abstractions, magic numbers | Bugs, slow development |
| **Architecture debt** | Boundaries or data store that no longer fit requirements | Scaling limits |
| **Test debt** | Low coverage, flaky tests, missing integration tests | Regressions ship |
| **Dependency debt** | Outdated libraries, unmaintained dependencies | Security vulns |
| **Documentation debt** | Missing runbooks, outdated READMEs, tribal knowledge | Onboarding pain |
| **Infrastructure debt** | Manual deploys, no monitoring, no IaC | Incidents, slow recovery |

## Prioritization Framework

Score each item on:

- **Impact**: How much does it slow the maintainer or team down? (1-5)
- **Risk**: What happens if we don't fix it? (1-5)
- **Effort**: How hard is the fix? (1-5, inverted — lower effort = higher priority)

Priority = (Impact + Risk) x (6 - Effort)

## Output

Produce a prioritized list with estimated effort, business justification for each item, and a phased remediation plan that can be done alongside feature work.
