from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from deliver_github_issues.workflow import (
    WorkflowError,
    clean_expired_summaries,
    execute_all_ready,
    execute_delivery,
    execute_issues,
    preview_all_ready,
    preview_issues,
    preview_queue,
    resume_delivery,
)

PREFLIGHT_EXIT = 10


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="deliver-github-issues")
    operations = parser.add_mutually_exclusive_group()
    operations.add_argument("--queue")
    operations.add_argument("--issues")
    operations.add_argument("--resume")
    operations.add_argument("--all-ready", action="store_true")
    operations.add_argument("--clean-summaries", action="store_true")
    parser.add_argument("--instruction", default="")
    parser.add_argument("--config")
    parser.add_argument("--primary-agent", choices=("codex", "claude"), default="codex")
    parser.add_argument("--metadata-agent", choices=("opencode", "kimi"), default="opencode")
    parser.add_argument("--keep-run-summary", action="store_true")
    parser.add_argument("--what-if", action="store_true")
    return parser


def run(arguments: Sequence[str] | None = None) -> int:
    argument_list = list(arguments) if arguments is not None else sys.argv[1:]
    try:
        args = build_parser().parse_args(argument_list)
    except SystemExit as error:
        return PREFLIGHT_EXIT if error.code else 0
    if not any((args.queue, args.issues, args.resume, args.all_ready, args.clean_summaries)):
        print(
            "one of --queue, --issues, --all-ready, --clean-summaries, or --resume is required",
            file=sys.stderr,
        )
        return PREFLIGHT_EXIT
    if args.instruction and not args.resume:
        print("--instruction requires --resume", file=sys.stderr)
        return PREFLIGHT_EXIT
    if args.resume and args.config:
        print("--config cannot be combined with --resume", file=sys.stderr)
        return PREFLIGHT_EXIT
    if args.resume and args.what_if:
        print("--what-if cannot be combined with --resume", file=sys.stderr)
        return PREFLIGHT_EXIT
    if args.resume and any(
        option in argument_list for option in ("--primary-agent", "--metadata-agent")
    ):
        print("agent selection cannot be combined with --resume", file=sys.stderr)
        return PREFLIGHT_EXIT
    if args.resume and "--keep-run-summary" in argument_list:
        print("--keep-run-summary cannot be combined with --resume", file=sys.stderr)
        return PREFLIGHT_EXIT
    config = args.config or ".github/deliver-github-issues.json"
    try:
        if args.what_if and args.queue:
            print(preview_queue(Path(args.queue), config, args.primary_agent, args.metadata_agent))
            return 0
        if args.what_if and args.issues:
            print(preview_issues(args.issues, config, args.primary_agent, args.metadata_agent))
            return 0
        if args.what_if and args.all_ready:
            print(preview_all_ready(config, args.primary_agent, args.metadata_agent))
            return 0
        if args.queue:
            count = execute_delivery(
                Path(args.queue),
                config,
                args.primary_agent,
                args.metadata_agent,
                args.keep_run_summary,
            )
            print(f"Delivered {count} issue(s) in queue order.")
            return 0
        if args.issues:
            count = execute_issues(
                args.issues,
                config,
                args.primary_agent,
                args.metadata_agent,
                args.keep_run_summary,
            )
            print(f"Delivered {count} issue(s) in queue order.")
            return 0
        if args.all_ready:
            count = execute_all_ready(
                config, args.primary_agent, args.metadata_agent, args.keep_run_summary
            )
            print(f"Delivered {count} issue(s) in queue order.")
            return 0
        if args.clean_summaries:
            count = clean_expired_summaries()
            print(f"Removed {count} expired run summary file(s).")
            return 0
        if args.resume:
            count = resume_delivery(args.resume, args.instruction)
            print(f"Delivered {count} issue(s) in queue order.")
            return 0
        raise WorkflowError("delivery mode is not implemented")
    except WorkflowError as error:
        print(error, file=sys.stderr)
        return error.exit_code


def main() -> None:
    raise SystemExit(run())


if __name__ == "__main__":
    main()
