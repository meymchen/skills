---
name: deploy-checklist
description: Prepare a release-specific deployment checklist with readiness checks and rollback triggers.
disable-model-invocation: true
user-invocable: true
argument-hint: "[service or release name]"
---

<!-- Adapted by Yuming Chen on 2026-10-04 for personal use and dual-host support. See ../../UPSTREAM.md. -->

# Deploy Checklist

> Read [CONNECTORS.md](../../CONNECTORS.md) for host invocation, personal use, and optional tools.

Generate a pre-deployment checklist to verify readiness before shipping.

## Usage

Claude Code examples are shown below. In Codex, select this plugin's `deploy-checklist` skill or use `$engineering:deploy-checklist` with the same request.

```text
/engineering:deploy-checklist [service or release name]
```

## Output

```markdown
## Deploy Checklist: [Service/Release]
**Date:** [Date] | **Deployer:** [Name]

### Pre-Deploy
- [ ] All tests passing in CI
- [ ] Code reviewed; required approvals obtained (if applicable)
- [ ] No known critical bugs in release
- [ ] Database migrations tested (if applicable)
- [ ] Feature flags configured (if applicable)
- [ ] Rollback plan documented
- [ ] On-call team notified (if applicable)

### Deploy
- [ ] Deploy to staging and verify (if available)
- [ ] Run smoke tests
- [ ] Deploy to production (canary if available)
- [ ] Monitor error rates and latency for the project's observation window
- [ ] Verify key user flows

### Post-Deploy
- [ ] Confirm metrics are nominal
- [ ] Update release notes / changelog
- [ ] Notify stakeholders (if applicable)
- [ ] Close related tickets

### Rollback Triggers
- Error rate exceeds [X]%
- P50 latency exceeds [X]ms
- [Critical user flow] fails
```

## Customization

Tell me about your deploy and I'll customize the checklist:

- "We use feature flags" → adds flag verification steps
- "This includes a database migration" → adds migration-specific checks
- "This is a breaking API change" → adds consumer notification steps

## If Connectors Available

If **~~source control** is connected:

- Pull the release diff and list of changes
- Verify release changes are merged and any required approvals are present

If **~~CI/CD** is connected:

- Check build and test status automatically
- Verify pipeline is green before deploy

If **~~monitoring** is connected:

- Pre-fill rollback trigger thresholds from current baselines
- Set up post-deploy metric watch

## Tips

1. **Run before every deploy** — Even routine ones. Checklists prevent "I forgot to..."
2. **Customize once, reuse** — Save the customized checklist in your project docs to reuse it next time.
3. **Include rollback criteria** — Decide when to roll back before you deploy, not during.
