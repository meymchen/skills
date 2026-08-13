# Python Scripts

How skills in this repository should create, run, and verify Python scripts
across Windows, macOS, and Linux.

## Scope

Apply these conventions to new Python code and to Python code already being
substantially changed. Do not rewrite an existing shell or PowerShell workflow
merely because a nearby file is being modified. A migration must be a deliberate
task whose benefit justifies changing its entry points, documentation, and tests.

Prefer Python for non-trivial, portable workflow logic. Bash and PowerShell
remain appropriate for small shell-native operations and operating-system
integration.

## Standalone scripts

Use PEP 723 inline metadata for a self-contained script, including scripts with
no third-party dependencies:

```python
# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
```

Run it with an explicit script invocation:

```console
uv run --script scripts/check.py
```

When the script has third-party dependencies, create and commit its adjacent
lock file:

```console
uv lock --script scripts/check.py
```

A standard-library-only script with `dependencies = []` does not need a lock
file. It may document a fallback to a compatible `python` executable when `uv`
is unavailable. A script with third-party dependencies or a lock file requires
`uv`; do not replace its environment setup with ad hoc `pip install` commands.

## Projects and tools

When a skill contains an internal Python package, shared modules, or multiple
entry points that share dependencies, manage the environment with a
`pyproject.toml` and `uv.lock` in the skill directory instead of duplicating PEP
723 metadata. Run commands that need the project environment with `uv run`, for
example `uv run pytest` or `uv run mypy`.

Use `uvx`, equivalently `uv tool run`, only for published Python CLI tools that
should run in an environment isolated from the project. Pin tool versions in
automation, for example:

```console
uvx ruff@0.14.0 check .
```

Human-oriented quick-start examples may omit the tool version. Automated entry
points must also pin a tested `uv` version. Human-oriented documentation should
state the minimum supported `uv` version without requiring an identical patch
release.

## Cross-platform code

- Use `pathlib` for path operations.
- Pass an argument list to subprocesses; do not assemble a command string.
- Do not use `shell=True` by default. Explain and test any necessary exception.
- Specify UTF-8 explicitly when reading or writing text.
- Use standard-library temporary-file APIs instead of assuming a platform's
  temporary directory.
- Resolve skill resources from the script's location, not the caller's current
  working directory.

Use `/` in relative paths shown in cross-platform command examples, such as
`scripts/check.py`. Avoid shell-specific variable assignments and path forms in
generic examples.

Platform-specific scripts are allowed when the platform dependency is inherent
to the task. State the supported platforms in the skill documentation and fail
early with a clear message on unsupported platforms.

## Verification

Call a script "cross-platform" only after its tests pass on at least Windows and
Ubuntu. Add macOS when the script interacts with macOS-specific paths,
permissions, or system facilities. Without that evidence, describe the script
as written portably rather than verified cross-platform.

When the first Python script is added, add a lightweight repository check that
verifies the applicable conventions, including PEP 723 metadata,
`requires-python`, required lock files, and documented invocation forms.

Document a local exception near the affected skill or code. Create an ADR only
when the exception is hard to reverse, surprising without context, and the
result of a genuine trade-off.
