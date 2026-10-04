---
name: standup
description: Summarize recent work as a standup update or personal progress note with next steps and blockers.
disable-model-invocation: true
user-invocable: true
argument-hint: "[yesterday | today | blockers]"
---

<!-- Adapted by Yuming Chen on 2026-10-04 for personal use and dual-host support. See ../../UPSTREAM.md. -->

# Standup

> Read [CONNECTORS.md](../../CONNECTORS.md) for host invocation, personal use, and optional tools.

Generate a standup update by pulling together recent activity across your tools. For solo work, use the same format as a personal progress note; no meeting or sharing is required.

## How It Works

```text
┌─────────────────────────────────────────────────────────────────┐
│                        STANDUP                                    │
├─────────────────────────────────────────────────────────────────┤
│  STANDALONE (always works)                                       │
│  ✓ Tell me what you worked on and I'll structure it             │
│  ✓ Format for daily standup (yesterday / today / blockers)      │
│  ✓ Keep it concise and action-oriented                          │
├─────────────────────────────────────────────────────────────────┤
│  SUPERCHARGED (when you connect your tools)                      │
│  + Source control: Recent commits and PRs                        │
│  + Project tracker: Ticket status changes                        │
│  + Chat: Relevant discussions and decisions                      │
│  + CI/CD: Build and deploy status                                │
└─────────────────────────────────────────────────────────────────┘
```

## What I Need From You

**Option A: Let me pull it**
If your tools are connected, invoke this skill and I'll gather everything automatically.

**Option B: Tell me what you did**
"Worked on the auth migration, reviewed 3 PRs, got blocked on the API rate limiting issue."

## Output

```markdown
## Standup — [Date]

### Yesterday
- [Completed item with ticket reference if available]
- [Completed item]

### Today
- [Planned item with ticket reference]
- [Planned item]

### Blockers
- [Blocker with context and next action; who can help if applicable]
```

## If Connectors Available

If **~~source control** is connected:

- Pull recent commits and PRs (opened, reviewed, merged)
- Summarize code changes at a high level

If **~~project tracker** is connected:

- Pull tickets moved to "in progress" or "done"
- Show upcoming planned work (sprint items if applicable)

If **~~chat** is connected:

- Scan for relevant discussions and decisions
- Flag threads needing your response

## Tips

1. **Run it every morning** — Build a habit and never scramble for standup notes.
2. **Add context** — After I generate, add any nuance about blockers or priorities.
3. **Share format** — Ask me to format for Slack, email, or your team's standup tool.
