from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

SKILL_ROOT = Path(__file__).parents[1]
SCRIPT = SKILL_ROOT / "scripts" / "verify_acceptance_items.py"

_spec = importlib.util.spec_from_file_location("verify_acceptance_items", SCRIPT)
assert _spec is not None and _spec.loader is not None
vai = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = vai  # dataclasses resolve annotations through sys.modules
_spec.loader.exec_module(vai)


class FakeGh:
    """Stands in for the gh CLI. Records every call; touches no network."""

    def __init__(
        self, responses: dict[str, object], failures: dict[str, str] | None = None
    ) -> None:
        self.responses = responses
        self.failures = failures or {}
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, args: list[str], stdin: str | None = None) -> str:
        self.calls.append((args, stdin))
        key = " ".join(args)
        for pattern, message in self.failures.items():
            if pattern in key:
                raise vai.SkillError(f"gh {key} failed: {message}", vai.EXIT_FAILED)
        for pattern, payload in self.responses.items():
            if pattern in key:
                return payload if isinstance(payload, str) else json.dumps(payload)
        raise AssertionError(f"unexpected gh call: {key}")


class ParseHeadingSectionsTests(unittest.TestCase):
    def test_atx_headings_produce_a_nested_heading_path(self):
        body = "## Acceptance criteria\n\n### Verdicts\n\n- [ ] first\n- [x] second\n"
        parsed = vai.parse_body(body)
        self.assertEqual(len(parsed["sections"]), 1)
        section = parsed["sections"][0]
        self.assertEqual(section["heading_path"], ["Acceptance criteria", "Verdicts"])
        self.assertEqual([item["text"] for item in section["items"]], ["first", "second"])
        self.assertEqual([item["checked"] for item in section["items"]], [False, True])
        self.assertEqual([item["line"] for item in section["items"]], [5, 6])

    def test_a_sibling_heading_closes_the_previous_section(self):
        body = "## One\n\n- [ ] a\n\n## Two\n\n- [ ] b\n"
        parsed = vai.parse_body(body)
        self.assertEqual([s["title"] for s in parsed["sections"]], ["One", "Two"])
        self.assertEqual(parsed["sections"][0]["heading_path"], ["One"])
        self.assertEqual(parsed["sections"][1]["heading_path"], ["Two"])

    def test_bold_line_acts_as_a_section_boundary(self):
        body = "## Acceptance criteria\n\n**Safety**\n\n- [ ] guarded\n"
        parsed = vai.parse_body(body)
        section = parsed["sections"][0]
        self.assertEqual(section["kind"], "bold")
        self.assertEqual(section["heading_path"], ["Acceptance criteria", "Safety"])

    def test_a_details_block_after_a_bold_line_is_its_sibling(self):
        body = (
            "## Criteria\n"
            "\n"
            "**Out of scope**\n"
            "\n"
            "- [ ] excluded\n"
            "\n"
            "<details><summary>Deferred</summary>\n"
            "\n"
            "- [ ] later\n"
            "\n"
            "</details>\n"
            "\n"
            "- [ ] after\n"
        )
        paths = [s["heading_path"] for s in vai.parse_body(body)["sections"]]
        self.assertEqual(
            paths, [["Criteria", "Out of scope"], ["Criteria", "Deferred"], ["Criteria"]]
        )

    def test_a_heading_after_a_leading_bold_line_is_not_nested_under_it(self):
        body = "**Note**\n\n- [ ] noted\n\n## Criteria\n\n- [ ] real\n"
        paths = [s["heading_path"] for s in vai.parse_body(body)["sections"]]
        self.assertEqual(paths, [["Note"], ["Criteria"]])

    def test_a_bold_line_inside_details_stays_inside_it(self):
        body = "<details><summary>Deferred</summary>\n\n**Later**\n\n- [ ] later\n\n</details>\n"
        paths = [s["heading_path"] for s in vai.parse_body(body)["sections"]]
        self.assertEqual(paths, [["Deferred", "Later"]])

    def test_a_line_with_two_bold_runs_is_not_a_section(self):
        body = "## Criteria\n\n**one** and **two**\n\n- [ ] item\n"
        parsed = vai.parse_body(body)
        self.assertEqual(parsed["sections"][0]["heading_path"], ["Criteria"])

    def test_details_block_acts_as_a_section_and_closes(self):
        body = (
            "## Criteria\n"
            "\n"
            "<details><summary>Deferred</summary>\n"
            "\n"
            "- [ ] hidden\n"
            "\n"
            "</details>\n"
            "\n"
            "- [ ] visible\n"
        )
        parsed = vai.parse_body(body)
        self.assertEqual(parsed["sections"][0]["heading_path"], ["Criteria", "Deferred"])
        self.assertEqual(parsed["sections"][0]["items"][0]["text"], "hidden")
        self.assertEqual(parsed["sections"][1]["heading_path"], ["Criteria"])
        self.assertEqual(parsed["sections"][1]["items"][0]["text"], "visible")

    def test_items_without_any_heading_are_still_reported(self):
        body = "- [ ] loose one\n- [ ] loose two\n"
        parsed = vai.parse_body(body)
        self.assertEqual(len(parsed["sections"]), 1)
        self.assertEqual(parsed["sections"][0]["heading_path"], [])
        self.assertEqual(parsed["sections"][0]["kind"], "none")
        self.assertEqual(len(parsed["sections"][0]["items"]), 2)

    def test_a_body_with_checkboxes_never_yields_an_empty_result(self):
        for body in (
            "- [ ] bare\n",
            "**Bold only**\n\n- [ ] under bold\n",
            "<details><summary>s</summary>\n\n- [ ] inside\n\n</details>\n",
            "Some prose.\n\n- [ ] after prose\n",
        ):
            with self.subTest(body=body):
                parsed = vai.parse_body(body)
                total = sum(len(section["items"]) for section in parsed["sections"])
                self.assertEqual(total, 1, f"lost the checkbox in: {body!r}")


class ParseItemDetailTests(unittest.TestCase):
    def test_sub_issue_entries_are_flagged(self):
        body = (
            "## Tasks\n"
            "\n"
            "- [ ] #123\n"
            "- [ ] owner/repo#7\n"
            "- [ ] https://github.com/owner/repo/issues/9\n"
            "- [ ] real acceptance item\n"
        )
        items = vai.parse_body(body)["sections"][0]["items"]
        self.assertEqual([item["sub_issue"] for item in items], [True, True, True, False])

    def test_nesting_depth_and_parent_are_recorded(self):
        body = "## Tasks\n\n- [ ] parent\n  - [ ] child\n    - [ ] grandchild\n- [ ] sibling\n"
        items = vai.parse_body(body)["sections"][0]["items"]
        self.assertEqual([item["depth"] for item in items], [0, 1, 2, 0])
        self.assertEqual(items[0]["parent"], None)
        self.assertEqual(items[1]["parent"], items[0]["id"])
        self.assertEqual(items[2]["parent"], items[1]["id"])
        self.assertEqual(items[3]["parent"], None)

    def test_wrapped_items_fold_into_text_while_raw_stays_the_anchor_line(self):
        body = (
            "## Criteria\n"
            "\n"
            "- [ ] Resolve candidate issues from closing references, then `#n`\n"
            "      mentions in the PR body, then numbers in the branch name.\n"
        )
        item = vai.parse_body(body)["sections"][0]["items"][0]
        self.assertEqual(
            item["raw"], "- [ ] Resolve candidate issues from closing references, then `#n`"
        )
        self.assertEqual(
            item["text"],
            "Resolve candidate issues from closing references, then `#n` "
            "mentions in the PR body, then numbers in the branch name.",
        )
        self.assertEqual(item["line"], 3)

    def test_crlf_bodies_parse_with_the_same_line_numbers(self):
        body = "## Criteria\r\n\r\n- [ ] one\r\n- [x] two\r\n"
        items = vai.parse_body(body)["sections"][0]["items"]
        self.assertEqual([item["line"] for item in items], [3, 4])
        self.assertEqual([item["text"] for item in items], ["one", "two"])
        self.assertEqual(items[0]["raw"], "- [ ] one")

    def test_empty_body_produces_no_sections(self):
        parsed = vai.parse_body("")
        self.assertEqual(parsed["sections"], [])
        self.assertEqual(parsed["skipped"], [])

    def test_checkboxes_in_tables_are_skipped_and_reported(self):
        body = "## Criteria\n\n| item | done |\n| --- | --- |\n| a | [ ] |\n\n- [ ] real\n"
        parsed = vai.parse_body(body)
        self.assertEqual([s["reason"] for s in parsed["skipped"]], ["table"])
        self.assertEqual(parsed["skipped"][0]["line"], 5)
        total = sum(len(section["items"]) for section in parsed["sections"])
        self.assertEqual(total, 1)

    def test_checkboxes_inside_fenced_code_are_ignored(self):
        body = "## Criteria\n\n```markdown\n- [ ] example in a code block\n```\n\n- [ ] real\n"
        parsed = vai.parse_body(body)
        items = [item for section in parsed["sections"] for item in section["items"]]
        self.assertEqual([item["text"] for item in items], ["real"])


class TickLinesTests(unittest.TestCase):
    def test_flips_only_the_requested_checkbox(self):
        body = "- [ ] one\n- [ ] two\n"
        updated, ticked, already = vai.tick_lines(body, [2])
        self.assertEqual(updated, "- [ ] one\n- [x] two\n")
        self.assertEqual(ticked, [2])
        self.assertEqual(already, [])

    def test_preserves_every_other_byte_of_the_body(self):
        body = (
            "<!-- keep me -->\n"
            "![screenshot](https://example.com/a.png)\n"
            "\n"
            "## Criteria\t\n"
            "\n"
            "- [ ] tick me   \n"
            "- [ ] leave me\n"
            "\n"
            "trailing spaces below   \n"
            "\n"
        )
        updated, ticked, _ = vai.tick_lines(body, [6])
        self.assertEqual(ticked, [6])
        self.assertEqual(updated, body.replace("- [ ] tick me", "- [x] tick me", 1))

    def test_preserves_crlf_line_endings(self):
        body = "- [ ] one\r\n- [ ] two\r\n"
        updated, ticked, _ = vai.tick_lines(body, [1])
        self.assertEqual(updated, "- [x] one\r\n- [ ] two\r\n")
        self.assertEqual(ticked, [1])

    def test_line_numbers_agree_with_extract_around_form_feeds(self):
        # str.splitlines() would also split on \f and shift every later line.
        body = "intro\fstill line one\n- [ ] two\n"
        self.assertEqual(vai.body_items(body)[2]["text"], "two")
        updated, ticked, _ = vai.tick_lines(body, [2])
        self.assertEqual(updated, "intro\fstill line one\n- [x] two\n")
        self.assertEqual(ticked, [2])

    def test_already_ticked_line_is_idempotent(self):
        body = "- [x] one\n"
        updated, ticked, already = vai.tick_lines(body, [1])
        self.assertEqual(updated, body)
        self.assertEqual(ticked, [])
        self.assertEqual(already, [1])

    def test_lines_that_are_not_tickable_items_are_refused(self):
        cases = {
            "prose": ("just prose\n", 1),
            "outside the body": ("- [ ] one\n", 9),
            "fenced code": ("```md\n- [ ] sample\n```\n", 2),
            "table": ("| a | [ ] |\n", 1),
            "sub-issue": ("- [ ] #123\n", 1),
        }
        for name, (body, line) in cases.items():
            with self.subTest(name), self.assertRaises(vai.SkillError) as caught:
                vai.tick_lines(body, [line])
            self.assertEqual(caught.exception.code, vai.EXIT_USAGE)


class RunGhTests(unittest.TestCase):
    def test_stdin_reaches_gh_byte_for_byte(self):
        # Text-mode pipes on Windows turn "\r\n" into "\r\r\n" on the way in.
        body = "- [x] one\r\n- [ ] two\nlast"
        completed = vai.subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")
        with mock.patch.object(vai.subprocess, "run", return_value=completed) as run:
            vai.run_gh(["issue", "edit", "7", "--body-file", "-"], body)
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["input"], body.encode("utf-8"))
        self.assertFalse(kwargs.get("text"))

    def test_stdout_is_decoded_as_utf8(self):
        completed = vai.subprocess.CompletedProcess(
            [], 0, stdout='{"body": "验收标准"}'.encode(), stderr=b""
        )
        with mock.patch.object(vai.subprocess, "run", return_value=completed):
            self.assertEqual(vai.run_gh(["issue", "view"]), '{"body": "验收标准"}')


class LineIdTests(unittest.TestCase):
    def test_accepts_extract_ids_and_bare_numbers(self):
        self.assertEqual(vai.line_id("L13"), 13)
        self.assertEqual(vai.line_id("13"), 13)

    def test_rejects_anything_else(self):
        for value in ("L0", "line13", "L-1", ""):
            with self.subTest(value), self.assertRaises(vai.argparse.ArgumentTypeError):
                vai.line_id(value)


def run_main(argv, gh):
    out = io.StringIO()
    with redirect_stdout(out), redirect_stderr(io.StringIO()):
        try:
            code = vai.main(list(argv), runner=gh)
        except SystemExit as exc:  # argparse usage errors
            code = exc.code
    return code, out.getvalue()


class ApplyCommandTests(unittest.TestCase):
    body = "## Criteria\n\n- [ ] one\n- [ ] two\n"

    def argv(self, *extra, sha=None):
        return [
            "apply",
            "--repo",
            "owner/repo",
            "--issue",
            "7",
            "--body-sha256",
            sha or vai.body_hash(self.body),
            "--tick",
            "L3",
            *extra,
        ]

    def test_writes_the_ticked_body_through_stdin(self):
        gh = FakeGh({"issue view": {"body": self.body}, "issue edit": ""})
        code, out = run_main(self.argv(), gh)
        self.assertEqual(code, vai.EXIT_OK)
        self.assertEqual(json.loads(out)["ticked"], [3])
        edit = [call for call in gh.calls if "edit" in call[0]][0]
        self.assertEqual(edit[0][-2:], ["--body-file", "-"])
        self.assertEqual(edit[1], "## Criteria\n\n- [x] one\n- [ ] two\n")

    def test_changed_body_hash_aborts_before_writing(self):
        gh = FakeGh({"issue view": {"body": self.body + "- [ ] three\n"}})
        code, _ = run_main(self.argv(), gh)
        self.assertEqual(code, vai.EXIT_PRECONDITION)
        self.assertFalse([call for call in gh.calls if "edit" in call[0]])

    def test_dry_run_makes_no_write(self):
        gh = FakeGh({"issue view": {"body": self.body}})
        code, out = run_main(self.argv("--dry-run"), gh)
        self.assertEqual(code, vai.EXIT_OK)
        self.assertTrue(json.loads(out)["dry_run"])
        self.assertFalse([call for call in gh.calls if "edit" in call[0]])

    def test_missing_write_permission_fails_as_a_precondition(self):
        gh = FakeGh(
            {"issue view": {"body": self.body}},
            failures={"issue edit": "HTTP 403: Resource not accessible by integration"},
        )
        code, _ = run_main(self.argv(), gh)
        self.assertEqual(code, vai.EXIT_PRECONDITION)

    def test_a_non_item_line_is_a_usage_error_and_nothing_is_written(self):
        gh = FakeGh({"issue view": {"body": self.body}})
        argv = self.argv()
        argv[argv.index("L3")] = "L1"
        code, _ = run_main(argv, gh)
        self.assertEqual(code, vai.EXIT_USAGE)
        self.assertFalse([call for call in gh.calls if "edit" in call[0]])

    def test_missing_hash_is_rejected_by_the_parser(self):
        gh = FakeGh({})
        code, _ = run_main(["apply", "--issue", "7", "--tick", "L3"], gh)
        self.assertNotEqual(code, vai.EXIT_OK)
        self.assertEqual(gh.calls, [])


class BriefCommandTests(unittest.TestCase):
    body = (
        "## Acceptance criteria\n"
        "\n"
        "- [x] parent criterion\n"
        "  - [ ] child criterion\n"
        "- [ ] #55\n"
        "- [ ] wrapped criterion\n"
        "      continues here\n"
    )

    def test_renders_only_the_chosen_items_with_their_ids(self):
        gh = FakeGh({"issue view 42": {"body": self.body}})
        code, out = run_main(
            ["brief", "--repo", "owner/repo", "--pr", "12", "--items", "#42:L3,L4,L6"], gh
        )
        self.assertEqual(code, vai.EXIT_OK)
        self.assertIn("pull request #12 in\n`owner/repo`", out)
        self.assertIn("gh pr diff 12 --repo owner/repo", out)
        self.assertIn(
            "### owner/repo#42\n\n"
            "- L3: parent criterion\n"
            "  - L4: child criterion\n"
            "- L6: wrapped criterion continues here\n",
            out,
        )
        self.assertNotIn("[x]", out.split("## Criteria", 1)[1])
        self.assertNotIn("$", out)

    def test_groups_items_by_issue_across_repositories(self):
        gh = FakeGh(
            {
                "issue view 42 --repo owner/repo": {"body": self.body},
                "issue view 3 --repo other/spec": {"body": "- [ ] spec item\n"},
            }
        )
        code, out = run_main(
            [
                "brief",
                "--repo",
                "owner/repo",
                "--pr",
                "12",
                "--items",
                "#42:L4",
                "other/spec#3:L1",
            ],
            gh,
        )
        self.assertEqual(code, vai.EXIT_OK)
        self.assertIn("### owner/repo#42\n\n- L4: child criterion", out)
        self.assertIn("### other/spec#3\n\n- L1: spec item", out)

    def test_a_sub_issue_or_non_item_selection_is_refused(self):
        for spec in ("#42:L5", "#42:L1", "#42:L0", "42:L3", "#42:"):
            with self.subTest(spec):
                gh = FakeGh({"issue view": {"body": self.body}})
                code, out = run_main(
                    ["brief", "--repo", "owner/repo", "--pr", "12", "--items", spec], gh
                )
                self.assertEqual(code, vai.EXIT_USAGE)
                self.assertEqual(out, "")


class ResolveLinksTests(unittest.TestCase):
    def gh_for(self, pr_payload, issues):
        responses: dict[str, object] = {"pr view": pr_payload}
        for path, payload in issues.items():
            responses[f"api repos/{path}"] = payload
        return FakeGh(responses)

    def test_closing_reference_is_high_trust(self):
        gh = self.gh_for(
            {
                "number": 12,
                "headRefName": "topic",
                "body": "",
                "closingIssuesReferences": [
                    {"number": 7, "url": "https://github.com/owner/repo/issues/7"}
                ],
            },
            {"owner/repo/issues/7": {"title": "T", "state": "open"}},
        )
        result = vai.resolve_links(gh, "owner/repo", 12)
        self.assertEqual(len(result["candidates"]), 1)
        candidate = result["candidates"][0]
        self.assertEqual(candidate["provenance"], "closing_reference")
        self.assertEqual(candidate["trust"], "high")
        self.assertEqual(candidate["number"], 7)

    def test_body_mention_and_branch_number_are_low_trust(self):
        gh = self.gh_for(
            {
                "number": 12,
                "headRefName": "fix-issue-31",
                "body": "Related to #22 and owner/other#5",
                "closingIssuesReferences": [],
            },
            {
                "owner/repo/issues/22": {"title": "A", "state": "open"},
                "owner/other/issues/5": {"title": "B", "state": "open"},
                "owner/repo/issues/31": {"title": "C", "state": "closed"},
            },
        )
        result = vai.resolve_links(gh, "owner/repo", 12)
        found = {(c["repo"], c["number"]): c for c in result["candidates"]}
        self.assertEqual(found[("owner/repo", 22)]["provenance"], "body_mention")
        self.assertEqual(found[("owner/other", 5)]["provenance"], "body_mention")
        self.assertEqual(found[("owner/repo", 31)]["provenance"], "branch_name")
        self.assertTrue(all(c["trust"] == "low" for c in result["candidates"]))

    def test_a_number_that_is_a_pull_request_is_rejected(self):
        gh = self.gh_for(
            {
                "number": 12,
                "headRefName": "topic",
                "body": "Follows #9",
                "closingIssuesReferences": [],
            },
            {"owner/repo/issues/9": {"title": "a PR", "pull_request": {"url": "..."}}},
        )
        result = vai.resolve_links(gh, "owner/repo", 12)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(len(result["rejected"]), 1)
        self.assertIn("pull request", result["rejected"][0]["reason"])

    def test_an_unresolvable_number_is_rejected_not_crashed(self):
        gh = FakeGh(
            {
                "pr view": {
                    "number": 12,
                    "headRefName": "topic",
                    "body": "See #404",
                    "closingIssuesReferences": [],
                }
            },
            failures={"api repos/owner/repo/issues/404": "HTTP 404: Not Found"},
        )
        result = vai.resolve_links(gh, "owner/repo", 12)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["rejected"][0]["number"], 404)

    def test_the_pull_request_does_not_reference_itself(self):
        gh = self.gh_for(
            {
                "number": 12,
                "headRefName": "branch-12",
                "body": "This is #12",
                "closingIssuesReferences": [],
            },
            {},
        )
        result = vai.resolve_links(gh, "owner/repo", 12)
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["rejected"], [])

    def test_a_cross_repository_closing_reference_keeps_its_own_repo(self):
        gh = self.gh_for(
            {
                "number": 12,
                "headRefName": "topic",
                "body": "",
                "closingIssuesReferences": [
                    {"number": 3, "url": "https://github.com/other/spec/issues/3"}
                ],
            },
            {"other/spec/issues/3": {"title": "spec", "state": "open"}},
        )
        result = vai.resolve_links(gh, "owner/repo", 12)
        self.assertEqual(result["candidates"][0]["repo"], "other/spec")


class LinksCommandTests(unittest.TestCase):
    pr_payload = {
        "number": 12,
        "headRefName": "topic",
        "body": "",
        "closingIssuesReferences": [{"number": 7, "url": "https://github.com/owner/repo/issues/7"}],
    }

    def test_without_pr_the_current_branch_pr_is_used(self):
        gh = FakeGh(
            {
                "repo view": {"nameWithOwner": "owner/repo"},
                "pr view": self.pr_payload,
                "api repos/owner/repo/issues/7": {"title": "T", "state": "open"},
            }
        )
        code, out = run_main(["links"], gh)
        self.assertEqual(code, vai.EXIT_OK)
        self.assertEqual(json.loads(out)["pr"], 12)
        pr_call = [call[0] for call in gh.calls if call[0][:2] == ["pr", "view"]][0]
        self.assertEqual(pr_call[2], "--json")

    def test_repo_without_pr_is_a_usage_error(self):
        gh = FakeGh({})
        code, _ = run_main(["links", "--repo", "owner/repo"], gh)
        self.assertEqual(code, vai.EXIT_USAGE)
        self.assertEqual(gh.calls, [])


class CommentTaskListTests(unittest.TestCase):
    def test_task_lists_in_comments_are_reported(self):
        comments = [
            {
                "url": "https://github.com/owner/repo/issues/7#issuecomment-1",
                "author": {"login": "someone"},
                "body": "Rough plan:\n\n- [ ] try the other approach\n- [x] measured it\n",
            }
        ]
        found = vai.comment_task_lists(comments)
        self.assertEqual([entry["reason"] for entry in found], ["comment", "comment"])
        self.assertEqual(found[0]["comment"], 1)
        self.assertEqual(found[0]["author"], "someone")
        self.assertEqual(found[0]["comment_line"], 3)
        self.assertEqual(found[0]["text"], "try the other approach")
        self.assertNotIn("line", found[0])

    def test_comments_without_task_lists_report_nothing(self):
        found = vai.comment_task_lists([{"body": "Looks good to me."}, {"body": ""}])
        self.assertEqual(found, [])

    def test_checkboxes_in_fenced_code_inside_a_comment_are_ignored(self):
        comments = [{"body": "Like this:\n\n```md\n- [ ] sample\n```\n"}]
        self.assertEqual(vai.comment_task_lists(comments), [])

    def test_extract_folds_comment_findings_into_skipped(self):
        gh = FakeGh(
            {
                "--json body": {"body": "## Criteria\n\n- [ ] real\n"},
                "--json comments": {
                    "comments": [{"body": "- [ ] noise", "author": {"login": "x"}, "url": "u"}]
                },
            }
        )
        out = io.StringIO()
        with redirect_stdout(out):
            code = vai.main(["extract", "--issue", "7", "--repo", "owner/repo"], runner=gh)
        self.assertEqual(code, vai.EXIT_OK)
        payload = json.loads(out.getvalue())
        self.assertEqual([entry["reason"] for entry in payload["skipped"]], ["comment"])
        body_items = [i for s in payload["sections"] for i in s["items"]]
        self.assertEqual([item["text"] for item in body_items], ["real"])

    def test_no_comments_flag_skips_the_extra_call(self):
        gh = FakeGh({"--json body": {"body": "- [ ] real\n"}})
        with redirect_stdout(io.StringIO()):
            code = vai.main(
                ["extract", "--issue", "7", "--repo", "owner/repo", "--no-comments"], runner=gh
            )
        self.assertEqual(code, vai.EXIT_OK)
        self.assertEqual(len(gh.calls), 1)


class ExtractCommandTests(unittest.TestCase):
    def test_extract_reports_the_hash_of_the_body_it_read(self):
        body = "## Criteria\n\n- [ ] one\n"
        gh = FakeGh({"issue view": {"body": body}})
        out = io.StringIO()
        with redirect_stdout(out):
            code = vai.main(["extract", "--issue", "7", "--repo", "owner/repo"], runner=gh)
        self.assertEqual(code, vai.EXIT_OK)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["body_sha256"], vai.body_hash(body))
        self.assertEqual(payload["issue"], 7)
        self.assertEqual(payload["sections"][0]["items"][0]["line"], 3)

    def test_repo_defaults_to_the_current_repository(self):
        gh = FakeGh(
            {"repo view": {"nameWithOwner": "owner/repo"}, "issue view": {"body": "- [ ] a\n"}}
        )
        with redirect_stdout(io.StringIO()):
            code = vai.main(["extract", "--issue", "7"], runner=gh)
        self.assertEqual(code, vai.EXIT_OK)
        self.assertEqual(gh.calls[0][0][:2], ["repo", "view"])


if __name__ == "__main__":
    unittest.main()
