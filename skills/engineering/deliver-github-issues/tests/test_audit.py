from __future__ import annotations

from pathlib import Path

import pytest

from deliver_github_issues.audit import AuditError, apply_satisfied_checkboxes, validate_audit


def test_audit_accepts_direct_file_command_and_ci_evidence(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "feature.py").write_text("pass\n", encoding="utf-8")
    checkboxes = [{"index": 0, "text": "works", "checked": False}]
    audit = {
        "summary": "verified",
        "criteria": [
            {
                "index": 0,
                "text": "works",
                "status": "satisfied",
                "evidence": [
                    {"kind": "file", "value": "src/feature.py:1"},
                    {"kind": "command", "value": "uv run pytest"},
                    {"kind": "ci", "value": "https://ci.test/1"},
                ],
            }
        ],
    }

    validate_audit(
        audit,
        checkboxes,
        tmp_path,
        successful_commands=["uv run pytest"],
        successful_ci_urls=["https://ci.test/1"],
    )


def test_audit_rejects_unverifiable_evidence(tmp_path: Path) -> None:
    checkboxes = [{"index": 0, "text": "works", "checked": False}]
    audit = {
        "summary": "claimed",
        "criteria": [
            {
                "index": 0,
                "text": "works",
                "status": "satisfied",
                "evidence": [{"kind": "file", "value": "missing.py"}],
            }
        ],
    }

    with pytest.raises(AuditError, match="Unverifiable evidence"):
        validate_audit(audit, checkboxes, tmp_path, [], [])


def test_only_satisfied_checkboxes_are_updated() -> None:
    body = "- [ ] automatic\n* [ ] needs judgement\n"
    audit = {
        "criteria": [
            {"index": 0, "text": "automatic", "status": "satisfied", "evidence": []},
            {"index": 1, "text": "needs judgement", "status": "human_required", "evidence": []},
        ]
    }

    assert apply_satisfied_checkboxes(body, audit) == "- [x] automatic\n* [ ] needs judgement\n"
