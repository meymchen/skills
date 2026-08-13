from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path


def isolated_agent_environment(bin_dir: Path) -> dict[str, str]:
    skills_home = bin_dir.parent / ".agents"
    locked: dict[str, object] = {"version": 3, "skills": {}}
    for name in ("implement", "tdd", "code-review"):
        skill_dir = skills_home / "skills" / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(f"---\nname: {name}\n---\n", encoding="utf-8")
        locked["skills"][name] = {
            "source": "mattpocock/skills",
            "skillFolderHash": f"test-{name}",
        }
    (skills_home / ".skill-lock.json").write_text(json.dumps(locked), encoding="utf-8")
    environment = os.environ.copy()
    environment["DGI_AGENTS_HOME"] = str(skills_home)
    environment["DGI_CLAUDE_HOME"] = str(skills_home)
    environment["PATH"] = str(bin_dir) + os.pathsep + environment["PATH"]
    return environment


def install_python_tool(directory: Path, name: str, source: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    program = directory / f"{name}_fake.py"
    program.write_text(source, encoding="utf-8")
    if os.name == "nt":
        wrapper = directory / f"{name}.cmd"
        wrapper.write_text(f'@echo off\r\n"{sys.executable}" "{program}" %*\r\n', encoding="utf-8")
    else:
        wrapper = directory / name
        wrapper.write_text(
            f"#!{sys.executable}\nexec(compile(open({str(program)!r}, encoding='utf-8').read(), {str(program)!r}, 'exec'))\n",
            encoding="utf-8",
        )
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
