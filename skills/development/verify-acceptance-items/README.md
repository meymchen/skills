# verify-acceptance-items

Check whether a pull request satisfies the acceptance criteria of the issues it
is linked to, then tick the ones the PR proves.

- [Usage and safety guide](../../../docs/development/verify-acceptance-items.md)
- [Skill entry point](SKILL.md)

One standard-library Python script does every deterministic step: resolving the
linked issues, reporting the issue body's task-list structure, rendering the
judging subagent's prompt, and ticking as a single character flip per line. The
agent decides which section holds the acceptance criteria, and a subagent with no
prior context judges whether each one is satisfied. Nothing is written until the
user confirms the verdicts.
