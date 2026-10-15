from __future__ import annotations

import importlib.util
import io
import json
import sys
import unittest
from collections.abc import Callable
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


class FakeRunner:
    """Stands in for git, gh, and glab. Records every call; touches no network.

    ``responses`` maps a substring of the joined command line to a payload, or
    to a callable taking ``(args, stdin)``; the first matching pattern wins.
    ``auth status`` succeeds for the CLIs in ``authenticated``.
    """

    def __init__(
        self,
        responses: dict[str, object] | None = None,
        failures: dict[str, str] | None = None,
        authenticated: tuple[str, ...] = ("gh", "glab"),
    ) -> None:
        self.responses = responses or {}
        self.failures = failures or {}
        self.authenticated = authenticated
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, args: list[str], stdin: str | None = None) -> str:
        self.calls.append((args, stdin))
        key = " ".join(args)
        for pattern, message in self.failures.items():
            if pattern in key:
                raise vai.SkillError(f"{key} failed: {message}", vai.EXIT_FAILED)
        if args[1:3] == ["auth", "status"]:
            if args[0] in self.authenticated:
                return ""
            raise vai.SkillError(f"{key} failed: not logged in", vai.EXIT_FAILED)
        for pattern, payload in self.responses.items():
            if pattern in key:
                if callable(payload):
                    payload = payload(args, stdin)
                return payload if isinstance(payload, str) else json.dumps(payload)
        raise AssertionError(f"unexpected call: {key}")

    def commands(self, program: str) -> list[list[str]]:
        return [args for args, _ in self.calls if args[0] == program]

    def platform_calls(self) -> list[list[str]]:
        return [
            args for args, _ in self.calls if args[0] != "git" and args[1:3] != ["auth", "status"]
        ]


class IssueStore:
    """One issue body that a fake write replaces, so read-back sees the result."""

    def __init__(self, body: str, mangle: Callable[[str], str] | None = None) -> None:
        self.body = body
        self.mangle = mangle or (lambda text: text)
        self.writes: list[str] = []

    def store(self, text: str) -> str:
        self.writes.append(text)
        self.body = self.mangle(text)
        return ""

    def github_view(self, args, stdin):
        return {"body": self.body}

    def github_edit(self, args, stdin):
        return self.store(stdin)

    def gitlab_get(self, args, stdin):
        return {"iid": 7, "description": self.body}

    def gitlab_put(self, args, stdin):
        self.store(json.loads(stdin)["description"])
        return {"iid": 7}


def git_remote(
    url: str, branch: str = "topic", remote: str | None = "origin", merge: str | None = None
) -> dict[str, object]:
    """Fake git answers for a branch whose upstream remote has ``url``."""
    responses: dict[str, object] = {"git rev-parse --abbrev-ref HEAD": f"{branch}\n"}
    responses[f"git config --get branch.{branch}.remote"] = (
        f"{remote}\n" if remote else _raise("no upstream")
    )
    responses[f"git config --get branch.{branch}.merge"] = (
        f"{merge}\n" if merge else _raise("no upstream")
    )
    responses[f"git remote get-url {remote or 'origin'}"] = f"{url}\n"
    return responses


def _raise(message: str):
    def fail(args, stdin):
        raise vai.SkillError(f"{' '.join(args)} failed: {message}", vai.EXIT_FAILED)

    return fail


def run_main(argv, runner):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = vai.main(list(argv), runner=runner)
        except SystemExit as exc:  # argparse usage errors
            code = exc.code
    run_main.stderr = err.getvalue()
    return code, out.getvalue()


GITHUB = ["--host", "github.com"]
GITLAB = ["--host", "gitlab.com"]
GL_PROJECT = "projects/group%2Fsub%2Fproject"


class ParseHeadingSectionsTests(unittest.TestCase):
    def test_atx_headings_produce_a_nested_heading_path(self):
        body = "## Acceptance criteria\n\n### Verdicts\n\n- [ ] first\n- [x] second\n"
        parsed = vai.parse_body(body)
        self.assertEqual(len(parsed["sections"]), 1)
        section = parsed["sections"][0]
        self.assertEqual(section["heading_path"], ["Acceptance criteria", "Verdicts"])
        self.assertEqual([item["text"] for item in section["items"]], ["first", "second"])
        self.assertEqual([item["state"] for item in section["items"]], ["unchecked", "checked"])
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
        items = vai.parse_body(body, "github.com")["sections"][0]["items"]
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


class TaskStateTests(unittest.TestCase):
    def test_every_item_reports_one_of_three_states(self):
        body = "- [ ] open\n- [x] done\n- [X] done too\n- [~] not applicable\n"
        items = vai.parse_body(body)["sections"][0]["items"]
        self.assertEqual(
            [item["state"] for item in items],
            ["unchecked", "checked", "checked", "inapplicable"],
        )
        self.assertTrue(all("checked" not in item for item in items))

    def test_an_inapplicable_item_is_never_ticked(self):
        with self.assertRaises(vai.SkillError) as caught:
            vai.tick_lines("- [~] skipped\n", [1])
        self.assertEqual(caught.exception.code, vai.EXIT_USAGE)


class ReferenceOnlyItemTests(unittest.TestCase):
    def test_references_to_another_issue_are_sub_issue_entries(self):
        for text in (
            "#12",
            "#12+",
            "#12+s",
            "owner/repo#12",
            "group/sub/project#12",
            "https://gitlab.example.com/group/sub/project/-/issues/12",
            "https://gitlab.example.com/group/project/-/work_items/12",
            "https://gitlab.example.com/group/project/issues/12",
            "https://gitlab.example.com/group/project/-/issues/12+",
        ):
            with self.subTest(text):
                body = f"- [ ] {text}\n"
                item = vai.parse_body(body, "gitlab.example.com")["sections"][0]["items"][0]
                self.assertTrue(item["sub_issue"])
                with self.assertRaises(vai.SkillError) as caught:
                    vai.tick_lines(body, [1], "gitlab.example.com")
                self.assertEqual(caught.exception.code, vai.EXIT_USAGE)

    def test_urls_on_another_host_or_with_more_text_are_criteria(self):
        for text in (
            "https://elsewhere.example/group/project/-/issues/12",
            "https://gitlab.example.com/group/project/-/merge_requests/12",
            "fix #12 first",
            "see group/project#12 for context",
        ):
            with self.subTest(text):
                parsed = vai.parse_body(f"- [ ] {text}\n", "gitlab.example.com")
                self.assertFalse(parsed["sections"][0]["items"][0]["sub_issue"])


class OrderedListTests(unittest.TestCase):
    def test_both_ordered_markers_are_parsed_and_ticked(self):
        body = "1. [ ] first\n2) [ ] second\n3. [x] third\n"
        items = vai.parse_body(body)["sections"][0]["items"]
        self.assertEqual([item["text"] for item in items], ["first", "second", "third"])
        self.assertEqual([item["depth"] for item in items], [0, 0, 0])
        updated, ticked, already = vai.tick_lines(body, [1, 2, 3])
        self.assertEqual(updated, "1. [x] first\n2) [x] second\n3. [x] third\n")
        self.assertEqual((ticked, already), ([1, 2], [3]))

    def test_nesting_follows_the_marker_width(self):
        cases = {
            "four columns under 10.": ("10. [ ] parent\n    - [ ] child\n", [0, 1]),
            "three columns under 10.": ("10. [ ] parent\n   - [ ] sibling\n", [0, 0]),
            "three columns under 1)": ("1) [ ] parent\n   1) [ ] child\n", [0, 1]),
            "two columns under 1.": ("1. [ ] parent\n  - [ ] sibling\n", [0, 0]),
            "one column under -": ("- [ ] parent\n - [ ] sibling\n", [0, 0]),
            "mixed markers": (
                "- [ ] a\n  1. [ ] b\n     - [ ] c\n  2. [ ] d\n",
                [0, 1, 2, 1],
            ),
        }
        for name, (body, depths) in cases.items():
            with self.subTest(name):
                items = vai.parse_body(body)["sections"][0]["items"]
                self.assertEqual([item["depth"] for item in items], depths)

    def test_parent_ids_follow_ordered_nesting(self):
        body = "10. [ ] parent\n    1. [ ] child\n"
        items = vai.parse_body(body)["sections"][0]["items"]
        self.assertEqual(items[1]["parent"], "L1")


class RunCommandTests(unittest.TestCase):
    def test_stdin_reaches_the_cli_byte_for_byte(self):
        # Text-mode pipes on Windows turn "\r\n" into "\r\r\n" on the way in.
        body = "- [x] one\r\n- [ ] two\nlast"
        completed = vai.subprocess.CompletedProcess([], 0, stdout=b"", stderr=b"")
        with mock.patch.object(vai.subprocess, "run", return_value=completed) as run:
            vai.run_command(["gh", "issue", "edit", "7", "--body-file", "-"], body)
        self.assertEqual(run.call_args.args[0][0], "gh")
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs["input"], body.encode("utf-8"))
        self.assertFalse(kwargs.get("text"))

    def test_stdout_is_decoded_as_utf8(self):
        completed = vai.subprocess.CompletedProcess(
            [], 0, stdout='{"body": "验收标准"}'.encode(), stderr=b""
        )
        with mock.patch.object(vai.subprocess, "run", return_value=completed):
            self.assertEqual(vai.run_command(["gh", "issue", "view"]), '{"body": "验收标准"}')

    def test_concatenated_pages_are_joined(self):
        joined = [{"a": 1}, {"a": 2}]
        self.assertEqual(vai.load_json('[{"a": 1}]\n[{"a": 2}]\n', ["glab"]), joined)
        self.assertEqual(vai.load_json('[{"a": 1}, {"a": 2}]', ["glab"]), joined)


class LineIdTests(unittest.TestCase):
    def test_accepts_extract_ids_and_bare_numbers(self):
        self.assertEqual(vai.line_id("L13"), 13)
        self.assertEqual(vai.line_id("13"), 13)

    def test_rejects_anything_else(self):
        for value in ("L0", "line13", "L-1", ""):
            with self.subTest(value), self.assertRaises(vai.argparse.ArgumentTypeError):
                vai.line_id(value)


class RemoteParsingTests(unittest.TestCase):
    def test_clone_and_web_urls_yield_host_and_project_path(self):
        cases = {
            "git@github.com:owner/repo.git": ("github.com", "owner/repo"),
            "https://github.com/owner/repo": ("github.com", "owner/repo"),
            "ssh://git@gitlab.example.com:2222/group/sub/project.git": (
                "gitlab.example.com",
                "group/sub/project",
            ),
            "https://gitlab.example.com:8443/group/sub/project.git": (
                "gitlab.example.com:8443",
                "group/sub/project",
            ),
            "https://gitlab.com/group/sub/project/-/issues/3": ("gitlab.com", "group/sub/project"),
            "git@GitLab.com:group/project.git": ("gitlab.com", "group/project"),
        }
        for url, expected in cases.items():
            with self.subTest(url):
                self.assertEqual(vai.parse_remote_url(url), expected)

    def test_repo_argument_accepts_paths_of_any_depth_and_urls(self):
        self.assertEqual(vai.split_repo_arg("owner/repo"), (None, "owner/repo"))
        self.assertEqual(
            vai.split_repo_arg("group/sub/deeper/project"), (None, "group/sub/deeper/project")
        )
        self.assertEqual(
            vai.split_repo_arg("https://gitlab.example.com/group/sub/project"),
            ("gitlab.example.com", "group/sub/project"),
        )
        for value in ("project", "a//b", "https://example.com/"):
            with self.subTest(value), self.assertRaises(vai.SkillError) as caught:
                vai.split_repo_arg(value)
            self.assertEqual(caught.exception.code, vai.EXIT_USAGE)


class PlatformDetectionTests(unittest.TestCase):
    body = "## Criteria\n\n- [ ] one\n"

    def extract(self, runner, *extra):
        return run_main(["extract", "--issue", "7", "--no-comments", *extra], runner)

    def github_responses(self):
        return {"gh issue view": {"body": self.body}}

    def gitlab_responses(self):
        return {"glab api": {"iid": 7, "description": self.body}}

    def test_github_com_remote_is_github(self):
        runner = FakeRunner(
            {**git_remote("git@github.com:owner/repo.git"), **self.github_responses()}
        )
        code, out = self.extract(runner)
        self.assertEqual(code, vai.EXIT_OK)
        payload = json.loads(out)
        self.assertEqual((payload["host"], payload["platform"]), ("github.com", "github"))
        self.assertEqual(payload["repo"], "owner/repo")
        self.assertEqual(runner.commands("glab"), [])

    def test_gitlab_hosts_are_gitlab_without_probing_gh(self):
        for url, host in (
            ("git@gitlab.com:group/sub/project.git", "gitlab.com"),
            ("https://gitlab.corp.example/group/project.git", "gitlab.corp.example"),
        ):
            with self.subTest(url):
                runner = FakeRunner({**git_remote(url), **self.gitlab_responses()})
                code, out = self.extract(runner)
                self.assertEqual(code, vai.EXIT_OK)
                payload = json.loads(out)
                self.assertEqual((payload["host"], payload["platform"]), (host, "gitlab"))
                self.assertEqual(runner.commands("gh"), [])

    def test_other_hosts_are_decided_by_the_authenticated_cli(self):
        for authenticated, platform in ((("gh",), "github"), (("glab",), "gitlab")):
            with self.subTest(platform):
                runner = FakeRunner(
                    {
                        **git_remote("https://code.example.com/group/project.git"),
                        **self.github_responses(),
                        **self.gitlab_responses(),
                    },
                    authenticated=authenticated,
                )
                code, out = self.extract(runner)
                self.assertEqual(code, vai.EXIT_OK)
                self.assertEqual(json.loads(out)["platform"], platform)
                probes = [args for args, _ in runner.calls if args[1:3] == ["auth", "status"]]
                self.assertIn(["gh", "auth", "status", "--hostname", "code.example.com"], probes)
                self.assertIn(["glab", "auth", "status", "--hostname", "code.example.com"], probes)

    def test_neither_or_both_authenticated_is_a_precondition_failure(self):
        for authenticated, word in (((), "neither"), (("gh", "glab"), "both")):
            with self.subTest(word):
                runner = FakeRunner(
                    git_remote("https://code.example.com/group/project.git"),
                    authenticated=authenticated,
                )
                code, out = self.extract(runner)
                self.assertEqual(code, vai.EXIT_PRECONDITION)
                self.assertEqual(out, "")
                self.assertIn(word, run_main.stderr)
                self.assertIn("--platform", run_main.stderr)
                self.assertEqual(runner.platform_calls(), [])

    def test_platform_flag_overrides_detection(self):
        runner = FakeRunner(
            {
                **git_remote("https://code.example.com/group/project.git"),
                **self.gitlab_responses(),
            },
            authenticated=("gh", "glab"),
        )
        code, out = self.extract(runner, "--platform", "gitlab")
        self.assertEqual(code, vai.EXIT_OK)
        self.assertEqual(json.loads(out)["platform"], "gitlab")
        self.assertEqual(runner.commands("gh"), [])

    def test_the_upstream_remote_wins_over_origin(self):
        runner = FakeRunner(
            {
                **git_remote(
                    "git@gitlab.com:group/project.git", remote="upstream", merge="refs/heads/topic"
                ),
                **self.gitlab_responses(),
            }
        )
        code, out = self.extract(runner)
        self.assertEqual(code, vai.EXIT_OK)
        self.assertIn(["git", "remote", "get-url", "upstream"], runner.commands("git"))
        self.assertEqual(json.loads(out)["platform"], "gitlab")

    def test_a_branch_without_upstream_falls_back_to_origin(self):
        runner = FakeRunner(
            {**git_remote("git@github.com:owner/repo.git", remote=None), **self.github_responses()}
        )
        code, _ = self.extract(runner)
        self.assertEqual(code, vai.EXIT_OK)
        self.assertIn(["git", "remote", "get-url", "origin"], runner.commands("git"))

    def test_host_flag_overrides_the_remote(self):
        runner = FakeRunner(self.gitlab_responses())
        code, out = self.extract(runner, "--host", "gitlab.com", "--repo", "group/sub/project")
        self.assertEqual(code, vai.EXIT_OK)
        self.assertEqual(json.loads(out)["host"], "gitlab.com")
        self.assertEqual(runner.commands("git"), [])

    def test_a_repo_url_supplies_the_host(self):
        runner = FakeRunner(self.gitlab_responses())
        code, out = self.extract(runner, "--repo", "https://gitlab.com/group/sub/project")
        self.assertEqual(code, vai.EXIT_OK)
        payload = json.loads(out)
        self.assertEqual((payload["host"], payload["repo"]), ("gitlab.com", "group/sub/project"))
        self.assertIn(f"{GL_PROJECT}/issues/7", runner.commands("glab")[-1])

    def test_missing_or_unauthenticated_cli_points_to_login(self):
        for host, cli in (("github.com", "gh"), ("gitlab.example.com", "glab")):
            with self.subTest(cli):
                runner = FakeRunner({}, authenticated=())
                code, _ = self.extract(runner, "--host", host, "--repo", "a/b")
                self.assertEqual(code, vai.EXIT_PRECONDITION)
                self.assertIn(f"{cli} auth login --hostname {host}", run_main.stderr)
                self.assertEqual(runner.platform_calls(), [])

    def test_no_github_literal_outside_platform_detection(self):
        source = SCRIPT.read_text(encoding="utf-8")
        start = source.index("# Platform detection")
        end = source.index("def parse_item_url")
        self.assertNotIn("github.com", source[:start] + source[end:])


class ApplyCommandTests(unittest.TestCase):
    body = "## Criteria\n\n- [ ] one\n- [ ] two\n- [~] skipped\n"

    def argv(self, *extra, tick="L3", platform=GITHUB, repo="owner/repo"):
        return [
            "apply",
            *platform,
            "--repo",
            repo,
            "--issue",
            "7",
            "--body-sha256",
            vai.body_hash(self.body),
            "--tick",
            tick,
            *extra,
        ]

    def github(self, store, failures=None):
        return FakeRunner(
            {"gh issue view": store.github_view, "gh issue edit": store.github_edit}, failures
        )

    def gitlab(self, store, failures=None):
        return FakeRunner({"-X PUT": store.gitlab_put, "/issues/7": store.gitlab_get}, failures)

    def test_github_writes_through_stdin_and_reads_back(self):
        store = IssueStore(self.body)
        runner = self.github(store)
        code, out = run_main(self.argv(), runner)
        self.assertEqual(code, vai.EXIT_OK)
        payload = json.loads(out)
        self.assertEqual(payload["ticked"], [3])
        self.assertEqual(payload["read_back"], "matched")
        self.assertEqual(payload["host"], "github.com")
        edit = [args for args in runner.commands("gh") if "edit" in args][0]
        self.assertEqual(edit[-4:], ["--repo", "github.com/owner/repo", "--body-file", "-"])
        self.assertEqual(store.writes, [self.body.replace("- [ ] one", "- [x] one")])
        views = [args for args in runner.commands("gh") if "view" in args]
        self.assertEqual(len(views), 2)

    def test_gitlab_sends_the_description_on_stdin_only(self):
        store = IssueStore(self.body)
        runner = self.gitlab(store)
        code, out = run_main(self.argv(platform=GITLAB, repo="group/sub/project"), runner)
        self.assertEqual(code, vai.EXIT_OK)
        self.assertEqual(json.loads(out)["read_back"], "matched")
        put_args, put_stdin = [call for call in runner.calls if "PUT" in call[0]][0]
        self.assertEqual(put_args[:2], ["glab", "api"])
        self.assertIn(f"{GL_PROJECT}/issues/7", put_args)
        self.assertEqual(put_args[-2:], ["--input", "-"])
        self.assertIn("Content-Type: application/json", put_args)
        self.assertFalse(any("[x]" in arg for arg in put_args))
        self.assertEqual(json.loads(put_stdin)["description"], store.writes[0])
        self.assertTrue(all(args[:2] == ["glab", "api"] for args in runner.platform_calls()))

    def test_a_read_back_mismatch_fails_on_both_platforms(self):
        for name, platform, factory in (
            ("github", GITHUB, self.github),
            ("gitlab", GITLAB, self.gitlab),
        ):
            with self.subTest(name):
                store = IssueStore(self.body, mangle=lambda text: text.replace("two", "2"))
                code, out = run_main(self.argv(platform=platform), factory(store))
                self.assertEqual(code, vai.EXIT_FAILED)
                self.assertEqual(out, "")
                self.assertIn("-- [ ] two", run_main.stderr)
                self.assertIn("+- [ ] 2", run_main.stderr)

    def test_line_ending_differences_pass_the_read_back(self):
        store = IssueStore(self.body, mangle=lambda text: text.replace("\n", "\r\n"))
        code, out = run_main(self.argv(platform=GITLAB), self.gitlab(store))
        self.assertEqual(code, vai.EXIT_OK)
        self.assertEqual(json.loads(out)["read_back"], "matched")

    def test_changed_body_hash_aborts_before_writing(self):
        store = IssueStore(self.body + "- [ ] three\n")
        code, _ = run_main(self.argv(), self.github(store))
        self.assertEqual(code, vai.EXIT_PRECONDITION)
        self.assertEqual(store.writes, [])

    def test_dry_run_makes_no_write(self):
        store = IssueStore(self.body)
        code, out = run_main(self.argv("--dry-run", platform=GITLAB), self.gitlab(store))
        self.assertEqual(code, vai.EXIT_OK)
        payload = json.loads(out)
        self.assertTrue(payload["dry_run"])
        self.assertIsNone(payload["read_back"])
        self.assertEqual(store.writes, [])

    def test_missing_write_permission_fails_as_a_precondition(self):
        cases = (
            ("github", GITHUB, self.github, "gh issue edit", "HTTP 403: Resource not accessible"),
            ("gitlab 403", GITLAB, self.gitlab, "-X PUT", "403 Forbidden"),
            (
                "gitlab scope",
                GITLAB,
                self.gitlab,
                "-X PUT",
                '{"error":"insufficient_scope","error_description":"requires api"}',
            ),
        )
        for name, platform, factory, pattern, message in cases:
            with self.subTest(name):
                store = IssueStore(self.body)
                runner = factory(store, {pattern: message})
                code, _ = run_main(self.argv(platform=platform), runner)
                self.assertEqual(code, vai.EXIT_PRECONDITION)
                self.assertIn("no write access", run_main.stderr)

    def test_other_write_failures_stay_failures(self):
        store = IssueStore(self.body)
        runner = self.gitlab(store, {"-X PUT": "500 Internal Server Error"})
        code, _ = run_main(self.argv(platform=GITLAB), runner)
        self.assertEqual(code, vai.EXIT_FAILED)

    def test_refused_lines_are_usage_errors_and_nothing_is_written(self):
        for tick in ("L1", "L5"):
            with self.subTest(tick):
                store = IssueStore(self.body)
                code, _ = run_main(self.argv(tick=tick), self.github(store))
                self.assertEqual(code, vai.EXIT_USAGE)
                self.assertEqual(store.writes, [])

    def test_missing_hash_is_rejected_by_the_parser(self):
        runner = FakeRunner({})
        code, _ = run_main(["apply", "--issue", "7", "--tick", "L3"], runner)
        self.assertNotEqual(code, vai.EXIT_OK)
        self.assertEqual(runner.calls, [])

    def test_the_pr_flag_has_no_alias(self):
        runner = FakeRunner({})
        code, _ = run_main(["links", "--pr", "12"], runner)
        self.assertNotEqual(code, vai.EXIT_OK)
        self.assertEqual(runner.calls, [])


class BriefCommandTests(unittest.TestCase):
    body = (
        "## Acceptance criteria\n"
        "\n"
        "- [x] parent criterion\n"
        "  - [ ] child criterion\n"
        "- [ ] #55\n"
        "- [ ] wrapped criterion\n"
        "      continues here\n"
        "- [~] dropped criterion\n"
    )

    def test_github_renders_only_the_chosen_items_with_their_ids(self):
        runner = FakeRunner({"gh issue view 42": {"body": self.body}})
        argv = ["brief", *GITHUB, "--repo", "owner/repo", "--request", "12"]
        code, out = run_main([*argv, "--items", "#42:L3,L4,L6"], runner)
        self.assertEqual(code, vai.EXIT_OK)
        self.assertIn("# Judge acceptance criteria against PR #12", out)
        self.assertIn("pull request #12 in `owner/repo` on `github.com`", out)
        self.assertIn("gh pr diff 12 --repo github.com/owner/repo", out)
        self.assertIn("check name", out)
        self.assertIn(
            "### owner/repo#42\n\n"
            "- L3: parent criterion\n"
            "  - L4: child criterion\n"
            "- L6: wrapped criterion continues here\n",
            out,
        )
        self.assertNotIn("[x]", out.split("## Criteria", 1)[1])
        self.assertNotIn("$", out)

    def test_gitlab_evidence_uses_glab_and_cites_jobs(self):
        runner = FakeRunner({f"{GL_PROJECT}/issues/42": {"description": self.body}})
        argv = ["brief", *GITLAB, "--repo", "group/sub/project", "--request", "9"]
        code, out = run_main([*argv, "--items", "#42:L4"], runner)
        self.assertEqual(code, vai.EXIT_OK)
        self.assertIn("# Judge acceptance criteria against MR !9", out)
        self.assertIn("merge request !9 in `group/sub/project` on `gitlab.com`", out)
        self.assertIn("glab mr diff 9 --raw --repo https://gitlab.com/group/sub/project", out)
        self.assertIn(f"{GL_PROJECT}/merge_requests/9/commits", out)
        self.assertIn("/jobs", out)
        self.assertIn("job name", out)
        self.assertNotIn("`gh ", out)
        self.assertNotIn("$", out)

    def test_selections_accept_nested_project_paths(self):
        runner = FakeRunner(
            {
                f"{GL_PROJECT}/issues/42": {"description": self.body},
                "projects/other%2Fdeep%2Fspec/issues/3": {"description": "- [ ] spec item\n"},
            }
        )
        argv = ["brief", *GITLAB, "--repo", "group/sub/project", "--request", "9"]
        code, out = run_main([*argv, "--items", "#42:L4", "other/deep/spec#3:L1"], runner)
        self.assertEqual(code, vai.EXIT_OK)
        self.assertIn("### group/sub/project#42\n\n- L4: child criterion", out)
        self.assertIn("### other/deep/spec#3\n\n- L1: spec item", out)

    def test_refused_selections_are_usage_errors(self):
        for spec in ("#42:L5", "#42:L8", "#42:L1", "#42:L0", "42:L3", "#42:"):
            with self.subTest(spec):
                runner = FakeRunner({"gh issue view": {"body": self.body}})
                argv = ["brief", *GITHUB, "--repo", "owner/repo", "--request", "12"]
                code, out = run_main([*argv, "--items", spec], runner)
                self.assertEqual(code, vai.EXIT_USAGE)
                self.assertEqual(out, "")


def github_pr(**overrides):
    payload = {
        "number": 12,
        "url": "https://github.com/owner/repo/pull/12",
        "headRefName": "topic",
        "body": "",
        "closingIssuesReferences": [],
    }
    payload.update(overrides)
    return payload


class GitHubLinksTests(unittest.TestCase):
    def links(self, pr_payload, issues, failures=None):
        responses: dict[str, object] = {"gh pr view": pr_payload}
        for path, payload in issues.items():
            responses[f"repos/{path}"] = payload
        runner = FakeRunner(responses, failures)
        argv = ["links", *GITHUB, "--repo", "owner/repo", "--request", "12"]
        code, out = run_main(argv, runner)
        self.assertEqual(code, vai.EXIT_OK, run_main.stderr)
        return json.loads(out), runner

    def test_closing_reference_is_high_trust(self):
        result, runner = self.links(
            github_pr(
                closingIssuesReferences=[
                    {"number": 7, "url": "https://github.com/owner/repo/issues/7"}
                ]
            ),
            {"owner/repo/issues/7": {"title": "T", "state": "open"}},
        )
        self.assertEqual(result["host"], "github.com")
        self.assertEqual((result["request"], result["request_label"]), (12, "PR #12"))
        candidate = result["candidates"][0]
        self.assertEqual(
            (candidate["provenance"], candidate["trust"], candidate["number"]),
            ("closing_reference", "high", 7),
        )
        self.assertIn(
            ["gh", "api", "--hostname", "github.com", "repos/owner/repo/issues/7"],
            runner.commands("gh"),
        )

    def test_body_mention_and_branch_number_are_low_trust(self):
        result, _ = self.links(
            github_pr(headRefName="fix-issue-31", body="Related to #22 and owner/other#5"),
            {
                "owner/repo/issues/22": {"title": "A", "state": "open"},
                "owner/other/issues/5": {"title": "B", "state": "open"},
                "owner/repo/issues/31": {"title": "C", "state": "closed"},
            },
        )
        found = {(c["repo"], c["number"]): c for c in result["candidates"]}
        self.assertEqual(found[("owner/repo", 22)]["provenance"], "body_mention")
        self.assertEqual(found[("owner/other", 5)]["provenance"], "body_mention")
        self.assertEqual(found[("owner/repo", 31)]["provenance"], "branch_name")
        self.assertTrue(all(c["trust"] == "low" for c in result["candidates"]))

    def test_a_number_that_is_a_pull_request_is_rejected(self):
        result, _ = self.links(
            github_pr(body="Follows #9"),
            {"owner/repo/issues/9": {"title": "a PR", "pull_request": {"url": "..."}}},
        )
        self.assertEqual(result["candidates"], [])
        self.assertIn("pull request", result["rejected"][0]["reason"])

    def test_an_unresolvable_number_is_rejected_not_crashed(self):
        result, _ = self.links(
            github_pr(body="See #404"), {}, {"repos/owner/repo/issues/404": "HTTP 404: Not Found"}
        )
        self.assertEqual(result["candidates"], [])
        self.assertEqual(result["rejected"][0]["number"], 404)

    def test_the_pull_request_does_not_reference_itself(self):
        result, _ = self.links(github_pr(headRefName="branch-12", body="This is #12"), {})
        self.assertEqual((result["candidates"], result["rejected"]), ([], []))

    def test_a_cross_repository_closing_reference_keeps_its_own_repo(self):
        result, _ = self.links(
            github_pr(
                closingIssuesReferences=[
                    {"number": 3, "url": "https://github.com/other/spec/issues/3"}
                ]
            ),
            {"other/spec/issues/3": {"title": "spec", "state": "open"}},
        )
        self.assertEqual(result["candidates"][0]["repo"], "other/spec")

    def test_an_issue_on_another_host_is_rejected_without_a_call(self):
        result, runner = self.links(
            github_pr(
                body="Spec: https://ghe.example.com/other/spec/issues/3",
                closingIssuesReferences=[
                    {"number": 4, "url": "https://ghe.example.com/other/spec/issues/4"}
                ],
            ),
            {},
        )
        self.assertEqual(result["candidates"], [])
        self.assertEqual(
            sorted((r["host"], r["number"]) for r in result["rejected"]),
            [("ghe.example.com", 3), ("ghe.example.com", 4)],
        )
        self.assertIn("ghe.example.com", result["rejected"][0]["reason"])
        self.assertFalse([args for args in runner.commands("gh") if args[1] == "api"])

    def test_without_request_the_current_branch_pr_is_used(self):
        runner = FakeRunner(
            {
                **git_remote("git@github.com:fork/repo.git"),
                "gh pr view": github_pr(
                    closingIssuesReferences=[
                        {"number": 7, "url": "https://github.com/owner/repo/issues/7"}
                    ]
                ),
                "repos/owner/repo/issues/7": {"title": "T", "state": "open"},
            }
        )
        code, out = run_main(["links"], runner)
        self.assertEqual(code, vai.EXIT_OK, run_main.stderr)
        payload = json.loads(out)
        self.assertEqual((payload["request"], payload["repo"]), (12, "owner/repo"))
        pr_call = [args for args in runner.commands("gh") if args[1:3] == ["pr", "view"]][0]
        self.assertEqual(pr_call[3], "--json")

    def test_repo_without_request_is_a_usage_error(self):
        runner = FakeRunner({})
        code, _ = run_main(["links", "--repo", "owner/repo"], runner)
        self.assertEqual(code, vai.EXIT_USAGE)
        self.assertEqual(runner.calls, [])


class GitLabLinksTests(unittest.TestCase):
    mr = f"{GL_PROJECT}/merge_requests/42"

    def issue(self, number, path="group/sub/project", host="gitlab.com"):
        return {
            "iid": number,
            "title": f"issue {number}",
            "web_url": f"https://{host}/{path}/-/issues/{number}",
        }

    def runner(self, closes=(), related=(), branch="topic", issues=None, extra=None):
        responses: dict[str, object] = dict(extra or {})
        responses[f"{self.mr}/closes_issues"] = list(closes)
        responses[f"{self.mr}/related_issues"] = list(related)
        responses[self.mr] = {"iid": 42, "source_branch": branch, "description": ""}
        responses.update(issues or {})
        return FakeRunner(responses)

    def links(self, runner):
        argv = ["links", *GITLAB, "--repo", "group/sub/project", "--request", "42"]
        code, out = run_main(argv, runner)
        self.assertEqual(code, vai.EXIT_OK, run_main.stderr)
        return json.loads(out)

    def test_closing_and_related_issues_come_from_the_api(self):
        runner = self.runner(
            closes=[self.issue(7)],
            related=[self.issue(7), self.issue(5, "other/spec")],
            issues={
                f"{GL_PROJECT}/issues/7": {"title": "seven", "state": "opened"},
                "projects/other%2Fspec/issues/5": {"title": "five", "state": "opened"},
            },
        )
        result = self.links(runner)
        self.assertEqual((result["host"], result["platform"]), ("gitlab.com", "gitlab"))
        self.assertEqual(result["request_label"], "MR !42")
        found = {
            (c["repo"], c["number"]): (c["provenance"], c["trust"]) for c in result["candidates"]
        }
        self.assertEqual(found[("group/sub/project", 7)], ("closing_reference", "high"))
        self.assertEqual(found[("other/spec", 5)], ("related_issue", "low"))
        self.assertTrue(all(args[:2] == ["glab", "api"] for args in runner.platform_calls()))
        paginated = [args for args in runner.platform_calls() if "--paginate" in args]
        self.assertEqual(len(paginated), 2)

    def test_the_branch_name_heuristic_runs_on_gitlab(self):
        runner = self.runner(
            branch="31-fix-it",
            issues={f"{GL_PROJECT}/issues/31": {"title": "C", "state": "opened"}},
        )
        result = self.links(runner)
        self.assertEqual(
            [(c["number"], c["provenance"]) for c in result["candidates"]], [(31, "branch_name")]
        )

    def test_an_issue_sharing_the_merge_request_number_is_kept(self):
        runner = self.runner(
            branch="42-topic",
            issues={f"{GL_PROJECT}/issues/42": {"title": "D", "state": "opened"}},
        )
        result = self.links(runner)
        self.assertEqual([c["number"] for c in result["candidates"]], [42])
        self.assertEqual(result["rejected"], [])

    def test_issues_on_another_host_or_tracker_are_rejected(self):
        runner = self.runner(
            closes=[
                self.issue(3, host="gitlab.example.com"),
                {"id": "JIRA-1", "title": "external"},
            ]
        )
        result = self.links(runner)
        self.assertEqual(result["candidates"], [])
        reasons = [r["reason"] for r in result["rejected"]]
        self.assertTrue(any("gitlab.example.com" in reason for reason in reasons))
        self.assertTrue(any("not an issue on gitlab.com" in reason for reason in reasons))

    def test_without_request_the_open_mr_for_the_branch_is_used(self):
        runner = self.runner(
            extra={
                **git_remote(
                    "git@gitlab.com:group/sub/project.git",
                    branch="local",
                    merge="refs/heads/topic",
                ),
                f"{GL_PROJECT}/merge_requests -X GET": [{"iid": 42}],
            }
        )
        code, out = run_main(["links"], runner)
        self.assertEqual(code, vai.EXIT_OK, run_main.stderr)
        self.assertEqual(json.loads(out)["request"], 42)
        lookup = [args for args in runner.commands("glab") if "source_branch=topic" in args]
        self.assertEqual(len(lookup), 1)

    def test_no_or_several_open_mrs_for_the_branch_is_a_precondition_failure(self):
        for found in ([], [{"iid": 42}, {"iid": 43}]):
            with self.subTest(len(found)):
                runner = FakeRunner(
                    {
                        **git_remote("git@gitlab.com:group/sub/project.git"),
                        f"{GL_PROJECT}/merge_requests -X GET": found,
                    }
                )
                code, _ = run_main(["links"], runner)
                self.assertEqual(code, vai.EXIT_PRECONDITION)
                self.assertIn("--request", run_main.stderr)


class CommentTaskListTests(unittest.TestCase):
    def test_task_lists_in_comments_are_reported(self):
        comments = [
            {
                "url": "https://example.com/owner/repo/issues/7#issuecomment-1",
                "author": "someone",
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

    def test_github_extract_folds_comment_findings_into_skipped(self):
        runner = FakeRunner(
            {
                "--json body": {"body": "## Criteria\n\n- [ ] real\n"},
                "--json comments": {
                    "comments": [{"body": "- [ ] noise", "author": {"login": "x"}, "url": "u"}]
                },
            }
        )
        argv = ["extract", *GITHUB, "--issue", "7", "--repo", "owner/repo"]
        code, out = run_main(argv, runner)
        self.assertEqual(code, vai.EXIT_OK)
        payload = json.loads(out)
        self.assertEqual(
            [(e["reason"], e["author"]) for e in payload["skipped"]], [("comment", "x")]
        )
        body_items = [i for s in payload["sections"] for i in s["items"]]
        self.assertEqual([item["text"] for item in body_items], ["real"])

    def test_gitlab_reads_every_page_of_notes_and_skips_system_notes(self):
        pages = (
            json.dumps([{"id": 1, "body": "- [ ] first page", "author": {"username": "a"}}])
            + "\n"
            + json.dumps(
                [
                    {"id": 2, "body": "- [ ] changed the description", "system": True},
                    {"id": 3, "body": "- [ ] second page", "author": {"username": "b"}},
                ]
            )
        )
        runner = FakeRunner(
            {
                f"{GL_PROJECT}/issues/7/notes": pages,
                f"{GL_PROJECT}/issues/7": {"description": "- [ ] real\n"},
            }
        )
        argv = ["extract", *GITLAB, "--issue", "7", "--repo", "group/sub/project"]
        code, out = run_main(argv, runner)
        self.assertEqual(code, vai.EXIT_OK, run_main.stderr)
        skipped = json.loads(out)["skipped"]
        self.assertEqual([e["text"] for e in skipped], ["first page", "second page"])
        self.assertEqual(
            skipped[1]["comment_url"], "https://gitlab.com/group/sub/project/-/issues/7#note_3"
        )
        notes_call = [args for args in runner.commands("glab") if "notes" in " ".join(args)][0]
        self.assertIn("--paginate", notes_call)

    def test_no_comments_flag_skips_the_extra_call(self):
        runner = FakeRunner({"--json body": {"body": "- [ ] real\n"}})
        argv = ["extract", *GITHUB, "--issue", "7", "--repo", "owner/repo", "--no-comments"]
        code, _ = run_main(argv, runner)
        self.assertEqual(code, vai.EXIT_OK)
        self.assertEqual(len(runner.platform_calls()), 1)


class ExtractCommandTests(unittest.TestCase):
    def test_extract_reports_the_hash_of_the_body_it_read(self):
        body = "## Criteria\n\n- [ ] one\n"
        runner = FakeRunner({"issue view": {"body": body}})
        argv = ["extract", *GITHUB, "--issue", "7", "--repo", "owner/repo", "--no-comments"]
        code, out = run_main(argv, runner)
        self.assertEqual(code, vai.EXIT_OK)
        payload = json.loads(out)
        self.assertEqual(payload["body_sha256"], vai.body_hash(body))
        self.assertEqual(payload["issue"], 7)
        self.assertEqual(payload["host"], "github.com")
        self.assertEqual(payload["sections"][0]["items"][0]["line"], 3)

    def test_issue_urls_on_the_detected_host_are_reference_only(self):
        body = (
            "- [ ] https://gitlab.com/group/sub/project/-/work_items/3\n"
            "- [ ] https://gitlab.example.com/group/sub/project/-/issues/3\n"
        )
        runner = FakeRunner({f"{GL_PROJECT}/issues/7": {"description": body}})
        argv = ["extract", *GITLAB, "--issue", "7", "--repo", "group/sub/project", "--no-comments"]
        code, out = run_main(argv, runner)
        self.assertEqual(code, vai.EXIT_OK)
        items = json.loads(out)["sections"][0]["items"]
        self.assertEqual([item["sub_issue"] for item in items], [True, False])

    def test_repo_defaults_to_the_remote_project(self):
        runner = FakeRunner(
            {
                **git_remote("git@gitlab.com:group/sub/project.git"),
                f"{GL_PROJECT}/issues/7": {"description": "- [ ] a\n"},
            }
        )
        code, out = run_main(["extract", "--issue", "7", "--no-comments"], runner)
        self.assertEqual(code, vai.EXIT_OK)
        self.assertEqual(json.loads(out)["repo"], "group/sub/project")


if __name__ == "__main__":
    unittest.main()
