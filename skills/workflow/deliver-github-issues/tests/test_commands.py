from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from deliver_github_issues import commands
from deliver_github_issues.commands import CommandError


def test_command_timeout_is_reported_as_a_command_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(commands.shutil, "which", lambda command: command)

    def expire(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired("agent", 300)

    monkeypatch.setattr(commands.subprocess, "run", expire)

    with pytest.raises(CommandError, match="timed out after 300 seconds"):
        commands.run_command("agent", timeout_seconds=300)


def test_command_retries_one_recognized_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(commands.shutil, "which", lambda command: command)
    results = iter(
        (
            subprocess.CompletedProcess([], 1, "", "temporarily unavailable"),
            subprocess.CompletedProcess([], 0, "done", ""),
        )
    )
    calls = 0

    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return next(results)

    monkeypatch.setattr(commands.subprocess, "run", run)

    result = commands.run_command("agent", transient_retries=1)

    assert calls == 2
    assert result.output == "done"


def test_npm_batch_shim_is_unwrapped_to_its_native_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(commands.os, "name", "nt")
    binary = tmp_path / "node_modules" / "agent-ai" / "bin" / "agent.exe"
    binary.parent.mkdir(parents=True)
    binary.write_text("", encoding="utf-8")
    shim = tmp_path / "agent.CMD"
    shim.write_text(
        '@ECHO off\n"%dp0%\\node_modules\\agent-ai\\bin\\agent.exe"   %*\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(commands.shutil, "which", lambda command: str(shim))

    assert commands._resolve_executable("agent") == str(binary)


def test_batch_shim_without_locatable_binary_is_kept(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(commands.os, "name", "nt")
    shim = tmp_path / "agent.CMD"
    shim.write_text("@ECHO off\necho no delegation here\n", encoding="utf-8")
    monkeypatch.setattr(commands.shutil, "which", lambda command: str(shim))

    assert commands._resolve_executable("agent") == str(shim)
