# verify-acceptance-items

Check whether a GitHub pull request or GitLab merge request (MR) satisfies the
acceptance criteria of the issues it is linked to, then tick the ones it proves.

- [Usage and safety guide](../../../docs/development/verify-acceptance-items.md)
- [Skill entry point](SKILL.md)

One standard-library Python script does every deterministic step: detecting
GitHub or GitLab from the branch's remote, resolving the linked issues,
reporting the issue body's task-list structure, rendering the judging
subagent's prompt, and ticking as a single character flip per line. A forge
adapter makes every platform call, through `gh` on GitHub and `glab api` on
GitLab. The agent decides which section holds the acceptance criteria, and a
subagent with no prior context judges whether each one is satisfied. Nothing is
written until the user confirms the verdicts.
