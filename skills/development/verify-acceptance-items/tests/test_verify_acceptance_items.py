from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

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
        updated, ticked, already = vai.tick_lines(body, [{"line": 2, "raw": "- [ ] two"}])
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
        updated, ticked, _ = vai.tick_lines(body, [{"line": 6, "raw": "- [ ] tick me   "}])
        self.assertEqual(ticked, [6])
        self.assertEqual(updated, body.replace("- [ ] tick me", "- [x] tick me", 1))
        before = body.split("\n")
        after = updated.split("\n")
        self.assertEqual(len(before), len(after))
        for index, (old, new) in enumerate(zip(before, after, strict=True)):
            if index != 5:
                self.assertEqual(old, new, f"line {index + 1} changed")

    def test_preserves_crlf_line_endings(self):
        body = "- [ ] one\r\n- [ ] two\r\n"
        updated, ticked, _ = vai.tick_lines(body, [{"line": 1, "raw": "- [ ] one"}])
        self.assertEqual(updated, "- [x] one\r\n- [ ] two\r\n")
        self.assertEqual(ticked, [1])

    def test_already_ticked_line_is_idempotent(self):
        body = "- [x] one\n"
        updated, ticked, already = vai.tick_lines(body, [{"line": 1, "raw": "- [ ] one"}])
        self.assertEqual(updated, body)
        self.assertEqual(ticked, [])
        self.assertEqual(already, [1])

    def test_changed_target_line_aborts(self):
        body = "- [ ] one, reworded\n"
        with self.assertRaises(vai.SkillError) as caught:
            vai.tick_lines(body, [{"line": 1, "raw": "- [ ] one"}])
        self.assertEqual(caught.exception.code, vai.EXIT_PRECONDITION)

    def test_line_outside_the_body_aborts(self):
        with self.assertRaises(vai.SkillError) as caught:
            vai.tick_lines("- [ ] one\n", [{"line": 9, "raw": "- [ ] one"}])
        self.assertEqual(caught.exception.code, vai.EXIT_PRECONDITION)

    def test_non_task_line_aborts(self):
        with self.assertRaises(vai.SkillError) as caught:
            vai.tick_lines("just prose\n", [{"line": 1, "raw": "just prose"}])
        self.assertEqual(caught.exception.code, vai.EXIT_PRECONDITION)


class ApplyCommandTests(unittest.TestCase):
    body = "## Criteria\n\n- [ ] one\n- [ ] two\n"

    def plan(self, **overrides):
        plan = {
            "repo": "owner/repo",
            "issue": 7,
            "body_sha256": vai.body_hash(self.body),
            "tick": [{"line": 3, "raw": "- [ ] one"}],
        }
        plan.update(overrides)
        return plan

    def run_apply(self, plan, gh, argv=("apply",)):
        stdin = io.StringIO(json.dumps(plan))
        original = vai.sys.stdin
        vai.sys.stdin = stdin
        out = io.StringIO()
        try:
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                code = vai.main(list(argv), runner=gh)
        finally:
            vai.sys.stdin = original
        return code, out.getvalue()

    def test_writes_the_ticked_body_through_stdin(self):
        gh = FakeGh({"issue view": {"body": self.body}, "issue edit": ""})
        code, out = self.run_apply(self.plan(), gh)
        self.assertEqual(code, vai.EXIT_OK)
        payload = json.loads(out)
        self.assertEqual(payload["ticked"], [3])
        edit = [call for call in gh.calls if "edit" in call[0]][0]
        self.assertIn("--body-file", edit[0])
        self.assertIn("-", edit[0])
        self.assertEqual(edit[1], "## Criteria\n\n- [x] one\n- [ ] two\n")

    def test_changed_body_hash_aborts_before_writing(self):
        gh = FakeGh({"issue view": {"body": self.body + "- [ ] three\n"}})
        code, _ = self.run_apply(self.plan(), gh)
        self.assertEqual(code, vai.EXIT_PRECONDITION)
        self.assertFalse([call for call in gh.calls if "edit" in call[0]])

    def test_dry_run_makes_no_write(self):
        gh = FakeGh({"issue view": {"body": self.body}})
        code, out = self.run_apply(self.plan(), gh, argv=("apply", "--dry-run"))
        self.assertEqual(code, vai.EXIT_OK)
        self.assertTrue(json.loads(out)["dry_run"])
        self.assertFalse([call for call in gh.calls if "edit" in call[0]])

    def test_missing_write_permission_fails_as_a_precondition(self):
        gh = FakeGh(
            {"issue view": {"body": self.body}},
            failures={"issue edit": "HTTP 403: Resource not accessible by integration"},
        )
        code, _ = self.run_apply(self.plan(), gh)
        self.assertEqual(code, vai.EXIT_PRECONDITION)

    def test_plan_without_a_hash_is_a_usage_error(self):
        gh = FakeGh({})
        plan = self.plan()
        del plan["body_sha256"]
        code, _ = self.run_apply(plan, gh)
        self.assertEqual(code, vai.EXIT_USAGE)

    def test_malformed_plan_is_a_usage_error(self):
        gh = FakeGh({})
        stdin = io.StringIO("{not json")
        original = vai.sys.stdin
        vai.sys.stdin = stdin
        try:
            with redirect_stderr(io.StringIO()):
                code = vai.main(["apply"], runner=gh)
        finally:
            vai.sys.stdin = original
        self.assertEqual(code, vai.EXIT_USAGE)


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
