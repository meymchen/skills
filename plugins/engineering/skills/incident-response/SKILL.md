---
name: incident-response
description: Run an incident response workflow covering triage, status updates, mitigation tracking, and postmortems.
disable-model-invocation: true
user-invocable: true
argument-hint: "<incident description or alert>"
---

<!-- Adapted by Yuming Chen on 2026-10-04 for personal use and dual-host support. See ../../UPSTREAM.md. -->

# Incident Response

> Read [CONNECTORS.md](../../CONNECTORS.md) for host invocation, personal use, and optional tools.

Manage an incident from detection through postmortem.

## Usage

Claude Code examples are shown below. In Codex, select this plugin's `incident-response` skill or use `$engineering:incident-response` with the same request.

```text
/engineering:incident-response <request>
```

## Modes

```text
/engineering:incident-response new [description]     # Start a new incident
/engineering:incident-response update [status]       # Draft a status update
/engineering:incident-response postmortem            # Generate postmortem from incident data
```

If the incident phase is not clear from the request or context, ask which phase to use.

## How It Works

```text
┌─────────────────────────────────────────────────────────────────┐
│                    INCIDENT RESPONSE                               │
├─────────────────────────────────────────────────────────────────┤
│  Phase 1: TRIAGE                                                  │
│  ✓ Assess severity (SEV1-4)                                     │
│  ✓ Identify affected systems and users                          │
│  ✓ Assign roles (IC, comms, responders)                         │
│                                                                    │
│  Phase 2: COMMUNICATE                                              │
│  ✓ Draft internal status update                                  │
│  ✓ Draft customer communication (if needed)                     │
│  ✓ Set up war room and cadence                                   │
│                                                                    │
│  Phase 3: MITIGATE                                                 │
│  ✓ Document mitigation steps taken                               │
│  ✓ Track timeline of events                                      │
│  ✓ Confirm resolution                                            │
│                                                                    │
│  Phase 4: POSTMORTEM                                               │
│  ✓ Blameless postmortem document                                 │
│  ✓ Timeline reconstruction                                       │
│  ✓ Root cause analysis (5 whys)                                  │
│  ✓ Action items with owners                                      │
└─────────────────────────────────────────────────────────────────┘
```

For a solo project, the maintainer covers the applicable roles. Keep a local timeline; omit war-room setup, paging, and internal updates when there is no team or audience.

## Severity Classification

Use the project's severity policy when available; otherwise treat these as suggested targets.

| Level | Criteria | Response Time |
| ------- | ---------- | --------------- |
| SEV1 | Service down, all users affected | Immediate; involve other responders if available |
| SEV2 | Major feature degraded, many users affected | Within 15 min |
| SEV3 | Minor feature issue, some users affected | Within 1 hour |
| SEV4 | Cosmetic or low-impact issue | Next business day |

## Communication Guidance

Provide clear, factual updates at regular cadence. Include: what's happening, who's affected, what we're doing, when the next update is.

## Output — Status Update

```markdown
## Incident Update: [Title]
**Severity:** SEV[1-4] | **Status:** Investigating | Identified | Monitoring | Resolved
**Impact:** [Who/what is affected]
**Last Updated:** [Timestamp]

### Current Status
[What we know now]

### Actions Taken
- [Action 1]
- [Action 2]

### Next Steps
- [What's happening next and ETA]

### Timeline
| Time | Event |
|------|-------|
| [HH:MM] | [Event] |
```

## Output — Postmortem

```markdown
## Postmortem: [Incident Title]
**Date:** [Date] | **Duration:** [X hours] | **Severity:** SEV[X]
**Authors:** [Names] | **Status:** Draft

### Summary
[2-3 sentence plain-language summary]

### Impact
- [Users affected]
- [Duration of impact]
- [Business impact if quantifiable]

### Timeline
| Time (UTC) | Event |
|------------|-------|
| [HH:MM] | [Event] |

### Root Cause
[Detailed explanation of what caused the incident]

### 5 Whys
1. Why did [symptom]? → [Because...]
2. Why did [cause 1]? → [Because...]
3. Why did [cause 2]? → [Because...]
4. Why did [cause 3]? → [Because...]
5. Why did [cause 4]? → [Root cause]

### What Went Well
- [Things that worked]

### What Went Poorly
- [Things that didn't work]

### Action Items
| Action | Owner | Priority | Due Date |
|--------|-------|----------|----------|
| [Action] | [Person] | P0/P1/P2 | [Date] |

### Lessons Learned
[Key takeaways for the maintainer or team]
```

## If Connectors Available

If **~~monitoring** is connected:

- Pull alert details and metrics
- Show graphs of affected metrics

If **~~incident management** is connected:

- Create or update incident in PagerDuty/Opsgenie
- Page on-call responders

If **~~chat** is connected:

- Post status updates to incident channel
- Create war room channel

## Tips

1. **Start writing immediately** — Don't wait for complete information. Update as you learn more.
2. **Keep updates factual** — What we know, what we've done, what's next. No speculation.
3. **Postmortems are blameless** — Focus on systems and processes, not individuals.
