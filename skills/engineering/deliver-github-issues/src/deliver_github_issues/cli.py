from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from deliver_github_issues.workflow import (
    WorkflowError,
    execute_delivery,
    execute_issues,
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
    parser.add_argument("--instruction", default="")
    parser.add_argument("--config")
    parser.add_argument("--what-if", action="store_true")
    return parser


def run(arguments: Sequence[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(arguments)
    except SystemExit as error:
        return PREFLIGHT_EXIT if error.code else 0
    if not any((args.queue, args.issues, args.resume)):
        print("one of --queue, --issues, or --resume is required", file=sys.stderr)
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
    config = args.config or ".github/deliver-github-issues.json"
    try:
        if args.what_if and args.queue:
            print(preview_queue(Path(args.queue), config))
            return 0
        if args.what_if and args.issues:
            print(preview_issues(args.issues, config))
            return 0
        if args.queue:
            count = execute_delivery(Path(args.queue), config)
            print(f"Delivered {count} issue(s) in queue order.")
            return 0
        if args.issues:
            count = execute_issues(args.issues, config)
            print(f"Delivered {count} issue(s) in queue order.")
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
