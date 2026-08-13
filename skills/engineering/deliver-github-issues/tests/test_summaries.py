from __future__ import annotations

import json
from pathlib import Path

import pytest

from deliver_github_issues import workflow


def test_permanent_summary_has_no_expiry(tmp_path: Path) -> None:
    run_dir = tmp_path / ".agent-runs" / "deliver-github-issues" / "20260813T120000Z-1234abcd"
    run_dir.mkdir(parents=True)
    delivery = workflow.DeliveryRun(
        tmp_path,
        run_dir,
        {
            "runId": run_dir.name,
            "repository": "acme/widgets",
            "agents": {
                "primary": "codex",
                "metadata": "opencode",
                "versions": {},
            },
            "completedIssues": [],
            "keepRunSummary": True,
        },
    )

    delivery.write_success_summary()

    summary = json.loads(
        (run_dir.parent / "summaries" / f"{run_dir.name}.json").read_text(encoding="utf-8")
    )
    assert summary["expiresAt"] is None


def test_cleanup_removes_only_expired_summaries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    summaries = tmp_path / ".agent-runs" / "deliver-github-issues" / "summaries"
    summaries.mkdir(parents=True)
    (summaries / "expired.json").write_text(
        json.dumps({"expiresAt": "2020-01-01T00:00:00Z"}), encoding="utf-8"
    )
    (summaries / "permanent.json").write_text(json.dumps({"expiresAt": None}), encoding="utf-8")
    monkeypatch.setattr(workflow, "repository_root", lambda: tmp_path)

    assert workflow.clean_expired_summaries() == 1
    assert not (summaries / "expired.json").exists()
    assert (summaries / "permanent.json").exists()
