# cleanup-merged-branch

Safely finish local work after GitHub has merged a PR and deleted its remote source
branch.

- [Usage and safety guide](../../../docs/routine/cleanup-merged-branch.md)
- [Skill entry point](SKILL.md)

The workflow is manual-only and uses one standard-library Python script. It verifies
the PR and branch identities before deleting fixed, ignored caches or local branches.
