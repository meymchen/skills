from __future__ import annotations

import os
import stat
import sys
from pathlib import Path


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
