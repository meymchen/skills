# verify-acceptance-items

Check whether a pull request satisfies the acceptance items of the issues it is
linked to, then tick the ones the PR proves.

- [Usage and safety guide](../../../docs/development/verify-acceptance-items.md)
- [Skill entry point](SKILL.md)

One standard-library Python script reports the issue body's task-list structure and
performs the tick as a single character flip per line; the agent decides which
section holds the acceptance items, and a subagent with no prior context judges
whether each one is satisfied. Nothing is written until the user confirms the
verdicts.
