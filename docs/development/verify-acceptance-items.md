# Verify acceptance items

`verify-acceptance-items` checks whether a request, meaning a GitHub pull
request or a GitLab merge request (MR), satisfies the acceptance criteria of the
issues it is linked to, and ticks the ones the request proves. A helper script
does every part with a deterministic answer. The agent keeps the two judgements
a script cannot make: which task-list items are acceptance criteria, and, in a
subagent that sees only the request, whether each one is satisfied.

[Read the skill source](../../skills/development/verify-acceptance-items/SKILL.md).

## Prerequisites

- `uv` 0.12.3 or newer.
- For GitHub.com or GitHub Enterprise Server: the GitHub CLI (`gh`),
  authenticated for the host with `gh auth login --hostname <host>`.
- For GitLab.com or self-managed GitLab: the GitLab CLI (`glab`), authenticated
  for the host with `glab auth login --hostname <host>`. The target is GitLab
  19.x. Items marked `[~]` need GitLab 15.8 or later to be stored and rendered.
  The script does not check GitLab or `glab` versions.
- Read access to the request and to every issue it references. Write access is
  needed only to tick; the report is produced without it.

The script is written portably for Windows, macOS, and Linux. Cross-platform
support is verified in Windows and Ubuntu CI; macOS remains POSIX-inferred until
exercised in macOS CI.

## Install

```console
npx skills@latest add meymchen/skills --skill verify-acceptance-items --agent codex --global
npx skills@latest add meymchen/skills --skill verify-acceptance-items --agent claude-code --global
npx skills@latest add meymchen/skills --skill verify-acceptance-items --agent opencode --global
```

Invoke it as `$verify-acceptance-items` in Codex or `/verify-acceptance-items` in
Claude Code and OpenCode. The agent also reaches for it on its own when asked
whether a PR or MR is done, when asked to check off an issue's checkboxes, or
right after opening a request that closes an issue; the confirmation step
protects the issue either way.

## Platform detection

Every command detects the platform from the host of the current branch's
upstream remote, or `origin` when the branch has none, using the same rules as
the git-workflows plugin:

- `github.com` is GitHub.
- `gitlab.com`, or any host containing `gitlab`, is GitLab.
- Any other host is decided by `gh auth status --hostname <host>` and
  `glab auth status --hostname <host>`: the CLI authenticated for the host
  decides. When neither or both are, the command exits with code 2 and the agent
  asks the user.

`--platform github|gitlab` overrides detection, and `--host` overrides the
detected host. `--repo` takes a project path of any depth, such as
`group/sub/project`, or a project URL, whose host is then used. The chosen CLI
must be installed and authenticated for the host; otherwise the command exits
with code 2 and names the `auth login` command to run. The script never falls
back to another tool or to a raw token.

Every JSON output includes `host` and `platform`. Only issues on the detected
host are followed; a linked issue on another host is listed in `rejected`.

## Subcommands

```console
uv run --script <skill-dir>/scripts/verify_acceptance_items.py links [--request 123]
uv run --script <skill-dir>/scripts/verify_acceptance_items.py extract --repo group/project --issue 42
uv run --script <skill-dir>/scripts/verify_acceptance_items.py brief --request 123 --items "#42:L13,L15" "group/sub/spec#3:L4"
uv run --script <skill-dir>/scripts/verify_acceptance_items.py apply --issue 42 --body-sha256 <hash> --tick L13 L15 [--dry-run]
```

`--repo` defaults to the project of the branch's remote.

- `links` reports each candidate issue with a `provenance` and a `trust`, and
  the request as `request` (its number) and `request_label` (PR #12 or MR !42).
  Without `--request` it uses the current branch's request: on GitHub the one
  `gh` finds for the branch, on GitLab the single open MR whose source branch is
  the branch's upstream branch.
  - On GitHub, `closing_reference` comes from the PR's closing references, and
    `body_mention` from `#n`, `owner/name#n`, and issue URLs in its body. A
    number that resolves to a pull request is rejected, since GitHub shares one
    number space between issues and PRs.
  - On GitLab, `closing_reference` comes from the MR's `closes_issues`, so an
    instance's custom closing pattern is respected, and `related_issue` comes
    from its `related_issues`. GitLab numbers issues and MRs separately, so no
    number is rejected for being an MR.
  - On both, `branch_name` takes numbers from the source branch name. Only
    `closing_reference` is high trust.
- `extract` reports the issue body's task-list structure, giving every item an
  `L<line>` id and a `state` of `unchecked`, `checked`, or `inapplicable`, and
  the `body_sha256` of the body it read. It never chooses an acceptance section.
- `brief` renders the judging subagent's complete prompt from the chosen item
  ids, one selection per issue. Item texts come from the issue itself, and their
  state is left out, because an existing tick is not evidence.
- `apply` ticks the given item ids in one issue.

Item ids are the only thing the agent passes between commands, so no criterion
text or line is ever retyped by the model.

Task lists with `-`, `*`, `+`, `1.`, and `1)` markers are parsed. Nesting is
measured from each marker's actual width, so a child of `10. [ ]` must be
indented four columns.

## Isolated judging

The verdicts are produced by one subagent with a fresh context, covering every
acceptance criterion of every issue. A session that just wrote the request
carries the intent behind the code, and that intent reads as evidence for
criteria the diff does not prove. The subagent's prompt is the output of
`brief`, passed on verbatim: the request, the evidence commands, the verdict
rules, and the criteria texts. Because the script writes it, the main session
has no place to add its own account of the request.

The evidence is the diff, the request's own prose, its commits, and its CI
results; the subagent neither checks out the branch nor runs tests. On GitHub
that is `gh pr diff`, `gh pr checks`, and `gh pr view`, and a `satisfied`
verdict may cite a check name. On GitLab it is `glab mr diff <iid> --raw`, the
MR's title, description, and commits, and the jobs of its head pipeline, read
through `glab api`; a `satisfied` verdict may cite a job name. The prompt's
wording lives in
[`assets/judge-brief.md`](../../skills/development/verify-acceptance-items/assets/judge-brief.md),
one set of rules whose `$evidence` placeholder the platform adapter fills.

The judging is not split per issue. Isolation comes from the fresh context rather
than from the number of subagents, and the cost of the run is dominated by reading
the request, a read each additional subagent would repeat to judge the same diff
against a different slice of the criteria.

The main session reports the verdicts rather than revising them. A disagreement
grounded in something outside the request is carried as `undecidable` and put to
the user, who can adjudicate it in the confirmation step; ticks made on that
basis are marked user-adjudicated rather than evidenced. An agent without
subagent support follows the brief itself and says in its report that the
judging was not isolated.

## Safety model

The only write is a single character flip per ticked line.

Before writing, `apply` re-reads the issue body and compares its SHA-256 against
the value `extract` reported. A mismatch aborts the run: somebody edited the issue
in the meantime, so the recorded line numbers can no longer be trusted. A match
means the body is byte-identical to the one `extract` numbered, so the ids alone
address the right lines. `apply` then parses the body the same way `extract` did
and refuses any id that is not a task-list item, which rules out prose, table
cells, fenced code, `[~]` items, and items that only reference another issue.
Ticking an item that is already `[x]` is reported as already checked rather than
treated as an error, which makes reruns safe.

The body is otherwise rewritten byte for byte, including line endings, images,
HTML comments, and trailing whitespace. The script never regenerates prose. The
new body travels on standard input, never as a command-line argument: `gh issue
edit --body-file -` on GitHub, and a JSON `description` sent with
`glab api --input -` on GitLab. After writing, `apply` reads the body back and
compares it with the text it sent, ignoring line-ending differences, which
either platform may normalise. A mismatch exits with code 1 and prints the
difference.

Three kinds of checkbox are deliberately out of scope and reported rather than
handled:

- Items that only reference another issue: `#12`, `#12+`, `#12+s`,
  `group/sub/project#12`, and issue or work-item URLs on the detected host, such
  as `.../issues/12`, `.../-/issues/12`, and `.../-/work_items/12`. Each is
  flagged with `sub_issue: true`.
- Checkboxes inside tables, listed in `skipped`.
- Task lists in issue comments, scanned in a second read and listed in
  `skipped` with a `reason` of `comment`; `--no-comments` turns this off. On
  GitLab every page of the issue's notes is read and system notes are skipped.
  A checkbox in a comment is as likely to be somebody's scratch list as a
  requirement, so it is surfaced rather than ticked.

Items already ticked are never unticked; when the request's evidence does not
support one, the skill warns and leaves it alone, because a tick may rest on
manual verification the diff cannot show.

## Exit codes

- `0`: the command completed;
- `1`: a `git`, `gh`, `glab`, or filesystem operation failed, including a
  read-back mismatch after a write;
- `2`: a precondition was not satisfied, including an ambiguous or
  unauthenticated platform, a changed issue body, and missing write access
  (HTTP 403 or insufficient token scope);
- `64`: the command line was invalid, including an item id that is not a
  tickable task-list item;
- `130`: the user interrupted the run.
