from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_TRANSIENT_FAILURE_MARKERS = (
    "connection reset",
    "rate limit",
    "temporarily unavailable",
    "temporary failure",
    "try again later",
)


class CommandError(RuntimeError):
    """An external command failed or returned invalid data."""


@dataclass(frozen=True)
class CommandResult:
    output: str
    stderr: str
    exit_code: int
    command_line: str


def run_command(
    command: str,
    arguments: list[str] | tuple[str, ...] = (),
    *,
    cwd: Path | None = None,
    log_path: Path | None = None,
    input_text: str | None = None,
    allow_failure: bool = False,
    env: dict[str, str] | None = None,
    timeout_seconds: int | None = None,
    transient_retries: int = 0,
) -> CommandResult:
    executable = shutil.which(command)
    if executable is None:
        raise CommandError(f"Required command is unavailable: {command}")
    argv = [executable, *arguments]
    rendered = shlex.join(argv)
    if log_path:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(f"\n> {rendered}\n")
    for attempt in range(transient_retries + 1):
        try:
            completed = subprocess.run(
                argv,
                cwd=cwd,
                input=input_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
                env=env,
                timeout=timeout_seconds,
            )
        except FileNotFoundError as error:
            raise CommandError(f"Required command is unavailable: {command}") from error
        except subprocess.TimeoutExpired as error:
            raise CommandError(
                f"Command timed out after {timeout_seconds} seconds: {rendered}"
            ) from error
        logged_output = completed.stdout + completed.stderr
        if log_path and logged_output:
            with log_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write(logged_output)
                if not logged_output.endswith("\n"):
                    stream.write("\n")
        transient = completed.returncode and any(
            marker in logged_output.lower() for marker in _TRANSIENT_FAILURE_MARKERS
        )
        if not transient or attempt == transient_retries:
            break
    if completed.returncode and not allow_failure:
        detail = f"\n{logged_output.rstrip()}" if logged_output.strip() else ""
        raise CommandError(f"Command failed ({completed.returncode}): {rendered}{detail}")
    return CommandResult(
        completed.stdout.rstrip("\r\n"),
        completed.stderr.rstrip("\r\n"),
        completed.returncode,
        shlex.join([command, *arguments]),
    )


def command_json(result: CommandResult, description: str) -> Any:
    try:
        return json.loads(result.output)
    except json.JSONDecodeError as error:
        raise CommandError(f"{description} returned invalid JSON: {error}") from error
