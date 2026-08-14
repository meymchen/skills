from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from fakes import install_python_tool, isolated_agent_environment


def install_fakes(bin_dir: Path) -> None:
    common = """import json, os, pathlib, sys
state = pathlib.Path(os.environ['DGI_FAKE_STATE'])
state.mkdir(parents=True, exist_ok=True)
tool = {tool!r}
args = sys.argv[1:]
with (state / 'calls.log').open('a', encoding='utf-8') as stream:
    stream.write(tool + ' ' + ' '.join(args) + '\\n')
"""
    git = (
        common.format(tool="git")
        + """
joined = ' '.join(args)
if args[:2] == ['rev-parse', '--show-toplevel']:
    print(os.getcwd()); raise SystemExit(0)
if args[:2] == ['branch', '--show-current']:
    print('agent/issue-79' if (state / 'branch').exists() else 'main'); raise SystemExit(0)
if args[:2] == ['status', '--porcelain']:
    if os.environ.get('DGI_FAKE_DIRTY') == '1' or ((state / 'changed').exists() and not (state / 'merged').exists()): print(' M changed')
    raise SystemExit(0)
if args[:2] == ['rev-parse', '--git-dir']:
    print('.git'); raise SystemExit(0)
if args[:2] == ['remote', 'get-url']:
    print('https://github.com/meymchen/lspf.git'); raise SystemExit(0)
if args and args[0] == 'show-ref': raise SystemExit(1)
if args and args[0] == 'for-each-ref':
    if (state / 'protected-ref-drift').exists(): print('refs/heads/main ' + 'b' * 40)
    raise SystemExit(0)
if args and args[0] == 'ls-remote':
    if (state / 'remote').exists(): print('a refs/heads/agent/issue-79'); raise SystemExit(0)
    raise SystemExit(2)
if args[:2] == ['switch', '-c']:
    (state / 'branch').touch(); raise SystemExit(0)
if args[:2] == ['switch', 'main']:
    (state / 'branch').unlink(missing_ok=True); raise SystemExit(0)
if args and args[0] == 'commit':
    (state / 'commit').touch(); raise SystemExit(0)
if args[:2] == ['rev-parse', 'HEAD']:
    print('a' * 40); raise SystemExit(0)
if args[:2] == ['diff', '--name-only']:
    if os.environ.get('DGI_FAKE_NO_CHANGES') != '1' and os.environ.get('DGI_FAKE_UNTRACKED') != '1': print('changed')
    raise SystemExit(0)
if args[:3] == ['ls-files', '--others', '--exclude-standard']:
    if os.environ.get('DGI_FAKE_UNTRACKED') == '1': print('new.py')
    raise SystemExit(0)
if args and args[0] == 'push' and '--set-upstream' in args:
    (state / 'remote').touch(); raise SystemExit(0)
if args and args[0] == 'push' and '--delete' in args:
    (state / 'remote').unlink(missing_ok=True); raise SystemExit(0)
raise SystemExit(0)
"""
    )
    gh = (
        common.format(tool="gh")
        + """
joined = ' '.join(args)
if args[:2] == ['auth', 'status']: raise SystemExit(0)
if args[:2] == ['repo', 'view']:
    print(json.dumps({'nameWithOwner':'meymchen/lspf','squashMergeAllowed':True,'defaultBranchRef':{'name':'main'}})); raise SystemExit(0)
if args[:2] == ['issue', 'view']:
    if any('blockedBy' in arg for arg in args):
        print(json.dumps({'number':79,'title':'Do thing','body':'- [ ] works',
            'updatedAt':'2026-08-13T00:00:00Z','state':'OPEN',
            'labels':[{'name':'ready-for-agent'}],
            'blockedBy':{'nodes':[],'totalCount':0},'blocking':{'nodes':[],'totalCount':0}})); raise SystemExit(0)
    accepted = os.environ.get('DGI_FAKE_ACCEPT') == '1'
    body = '- [ ] changed' if os.environ.get('DGI_FAKE_BODY_DRIFT') == '1' else ('- [x] works' if accepted else '- [ ] works')
    print(json.dumps({'number':79,'title':'Do thing','body':body,
        'labels':[{'name':'ready-for-agent'}],'updatedAt':'2026-08-13T00:00:00Z',
        'state':'CLOSED' if (state / 'merged').exists() else 'OPEN',
        'comments':[{'author':{'login':'operator'},'body':'/accept ' + 'a' * 40}] if accepted else [],
        'url':'https://github.test/issues/79'})); raise SystemExit(0)
if args[:2] == ['pr', 'list']: print('[]'); raise SystemExit(0)
if args[:2] == ['pr', 'create']: print('https://github.test/pr/1'); raise SystemExit(0)
if args[:2] == ['pr', 'checks']:
    bucket = 'fail' if os.environ.get('DGI_FAKE_CI_FAIL') == '1' else 'pass'
    print(json.dumps([{'name':'test','bucket':bucket,'state':'SUCCESS','link':'https://ci.test/1'}])); raise SystemExit(0)
if args[:2] == ['pr', 'merge']:
    (state / 'merged').touch(); raise SystemExit(0)
if args[:2] == ['pr', 'view']:
    value = {'number':1,'url':'https://github.test/pr/1','headRefOid':'e' * 40 if os.environ.get('DGI_FAKE_HEAD_DRIFT') == '1' else 'a' * 40,
        'isDraft':False,'mergeStateStatus':'DIRTY' if os.environ.get('DGI_FAKE_CONFLICT') == '1' else 'CLEAN','reviewDecision':'APPROVED',
        'state':'MERGED' if (state / 'merged').exists() else 'OPEN'}
    if (state / 'merged').exists(): value['mergeCommit'] = {'oid':'b' * 40}
    print(json.dumps(value)); raise SystemExit(0)
if args[:2] in (['issue', 'edit'], ['issue', 'comment']): raise SystemExit(0)
if args[:2] == ['api', 'user']: print(json.dumps({'login':'operator'})); raise SystemExit(0)
raise SystemExit(0)
"""
    )
    codex = (
        common.format(tool="codex")
        + """
if args == ['--version']: print('codex-cli 0.147.0'); raise SystemExit(0)
if args[:2] == ['login', 'status']: print('authenticated'); raise SystemExit(0)
if any('CAPABILITY_PROBE' in arg for arg in args):
    message = 'typechecking; full test suite; code-review; current branch'
    out = pathlib.Path(args[args.index('--output-last-message') + 1])
    out.write_text(message, encoding='utf-8'); print(message); raise SystemExit(0)
out = pathlib.Path(args[args.index('--output-last-message') + 1])
schema = args[args.index('--output-schema') + 1]
if schema.endswith('implement.schema.json'):
    if os.environ.get('DGI_FAKE_NO_CHANGES') != '1': (state / 'changed').touch()
    if os.environ.get('DGI_FAKE_PRIMARY_PUSH') == '1': (state / 'remote').touch()
    value = {'status':'completed','summary':'implemented','commitSha':None,
        'changedFiles':(['wrong'] if os.environ.get('DGI_FAKE_BAD_HANDOFF') == '1' else
            (['new.py'] if os.environ.get('DGI_FAKE_UNTRACKED') == '1' else ['changed'])),
        'reviewSummary':'initial review','usedSkills':['implement','tdd','code-review'],
        'tests':[{'command':'targeted','exitCode':0}],'blockers':[]}
elif schema.endswith('review.schema.json'):
    value = {'status':'passed','summary':'reviewed','usedSkills':['code-review'],'findings':[]}
else:
    if os.environ.get('DGI_FAKE_AUDIT_PUSH') == '1': (state / 'protected-ref-drift').touch()
    if os.environ.get('DGI_FAKE_HUMAN') == '1':
        value = {'summary':'human','criteria':[{'index':0,'text':'works','status':'human_required','evidence':[]}]}
    else:
        value = {'summary':'accepted','criteria':[{'index':0,'text':'works','status':'satisfied',
            'evidence':[{'kind':'command','value':'check ok'}]}]}
out.write_text(json.dumps(value), encoding='utf-8')
print(json.dumps({'type':'result'})); raise SystemExit(0)
"""
    )
    check = (
        common.format(tool="check")
        + """
counter = state / 'check-count'
count = int(counter.read_text() if counter.exists() else '0') + 1
counter.write_text(str(count))
fail_final_once = os.environ.get('DGI_FAKE_FINAL_LOCAL_FAIL') == '1' and count == 2
raise SystemExit(9 if os.environ.get('DGI_FAKE_LOCAL_FAIL') == '1' or fail_final_once else 0)
"""
    )
    opencode = (
        common.format(tool="opencode")
        + """
if args == ['--version']: print('1.18.18'); raise SystemExit(0)
if args[:2] == ['debug', 'config']:
    print(json.dumps({'tools':{'*':False},'permission':{'*':'deny'}})); raise SystemExit(0)
if args[:2] == ['debug', 'agent']:
    print(json.dumps({'tools':{'*':False},'permission':{'*':'deny'}})); raise SystemExit(0)
if any('METADATA_CAPABILITY_OK' in arg for arg in args):
    print(json.dumps({'type':'text','part':{'text':'METADATA_CAPABILITY_OK'}})); raise SystemExit(0)
value = {'commitTitle':'Do thing (#79)','prTitle':'Do thing (#79)','summary':'implemented'}
print(json.dumps({'type':'text','part':{'text':json.dumps(value)}})); raise SystemExit(0)
"""
    )
    claude = (
        common.format(tool="claude")
        + """
if args == ['--version']: print('2.1.227'); raise SystemExit(0)
if args[:2] == ['auth', 'status']: print('authenticated'); raise SystemExit(0)
if any('CAPABILITY_PROBE' in arg for arg in args):
    print('typechecking; full test suite; code-review; current branch'); raise SystemExit(0)
prompt = sys.stdin.read()
if '\"phase\": \"implement\"' in prompt:
    (state / 'changed').touch()
    value = {'status':'completed','summary':'implemented by claude','commitSha':None,
        'changedFiles':['changed'],'reviewSummary':'initial review','usedSkills':['implement','code-review'],
        'tests':[{'command':'targeted','exitCode':0}],'blockers':[]}
elif '\"phase\": \"review\"' in prompt:
    value = {'status':'passed','summary':'reviewed','usedSkills':['code-review'],'findings':[]}
else:
    value = {'summary':'accepted','criteria':[{'index':0,'text':'works','status':'satisfied',
        'evidence':[{'kind':'command','value':'check ok'}]}]}
print(json.dumps({'structured_output':value})); raise SystemExit(0)
"""
    )
    for name, source in (
        ("git", git),
        ("gh", gh),
        ("codex", codex),
        ("claude", claude),
        ("opencode", opencode),
        ("check", check),
    ):
        install_python_tool(bin_dir, name, source)


def test_happy_path_is_ordered_and_removes_successful_state(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    config_dir = tmp_path / ".scratch"
    config_dir.mkdir()
    policy = {
        "version": 1,
        "readyLabel": "ready-for-agent",
        "branchPrefix": "agent/issue-",
        "ciTimeoutMinutes": 1,
        "primaryTimeoutMinutes": 60,
        "metadataTimeoutMinutes": 5,
        "maxPrimaryFixAttempts": 3,
        "localChecks": [{"name": "test", "command": "check", "arguments": ["ok"]}],
        "requiredChecks": ["test"],
    }
    (config_dir / "deliver-github-issues.json").write_text(json.dumps(policy), encoding="utf-8")
    queue_path = tmp_path / "queue.json"
    queue_path.write_text(
        json.dumps(
            {
                "version": 1,
                "repository": "meymchen/lspf",
                "baseBranch": "main",
                "issues": [{"number": 79, "skills": ["tdd"], "instruction": ""}],
            }
        ),
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    install_fakes(fake_bin)
    environment = isolated_agent_environment(fake_bin)
    environment["DGI_FAKE_STATE"] = str(tmp_path / "fake-state")

    result = subprocess.run(
        [sys.executable, "-m", "deliver_github_issues.cli", "--queue", str(queue_path)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert "Delivered 1 issue(s) in queue order." in result.stdout
    calls = (tmp_path / "fake-state" / "calls.log").read_text(encoding="utf-8")
    expected = [
        "git switch -c agent/issue-79",
        "codex exec --ephemeral --sandbox workspace-write",
        "check ok",
        "git reset --soft",
        "gh pr create",
        "gh pr checks",
        "gh pr merge",
        "git push origin --delete agent/issue-79",
    ]
    positions = [calls.index(item) for item in expected]
    assert positions == sorted(positions)
    assert sum(line == "check ok" for line in calls.splitlines()) == 2
    assert "review.schema.json" in calls
    assert "--match-head-commit " + "a" * 40 in calls
    runs = tmp_path / ".agent-runs" / "deliver-github-issues"
    assert not any(path.is_dir() and path.name != "summaries" for path in runs.iterdir())
    summary = json.loads(next((runs / "summaries").glob("*.json")).read_text(encoding="utf-8"))
    assert summary["agents"]["primary"] == "codex"
    assert summary["agents"]["metadata"] == "opencode"
    assert summary["agents"]["versions"]["opencode"] == "1.18.18"
    assert summary["issues"][0]["number"] == 79


def test_human_gate_preserves_state_and_requires_exact_acceptance(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    config_dir = tmp_path / ".scratch"
    config_dir.mkdir()
    policy = {
        "version": 1,
        "readyLabel": "ready-for-agent",
        "branchPrefix": "agent/issue-",
        "ciTimeoutMinutes": 1,
        "primaryTimeoutMinutes": 60,
        "metadataTimeoutMinutes": 5,
        "maxPrimaryFixAttempts": 3,
        "localChecks": [{"name": "test", "command": "check", "arguments": ["ok"]}],
        "requiredChecks": ["test"],
    }
    (config_dir / "deliver-github-issues.json").write_text(json.dumps(policy), encoding="utf-8")
    queue_path = tmp_path / "queue.json"
    queue_path.write_text(
        json.dumps(
            {
                "version": 1,
                "repository": "meymchen/lspf",
                "baseBranch": "main",
                "issues": [{"number": 79, "skills": ["tdd"], "instruction": ""}],
            }
        ),
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    install_fakes(fake_bin)
    environment = isolated_agent_environment(fake_bin)
    environment["DGI_FAKE_STATE"] = str(tmp_path / "fake-state")
    environment["DGI_FAKE_HUMAN"] = "1"

    stopped = subprocess.run(
        [sys.executable, "-m", "deliver_github_issues.cli", "--queue", str(queue_path)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert stopped.returncode == 40
    run_dir = next((tmp_path / ".agent-runs" / "deliver-github-issues").iterdir())
    state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "awaiting_human"
    environment["DGI_FAKE_ACCEPT"] = "1"

    resumed = subprocess.run(
        [sys.executable, "-m", "deliver_github_issues.cli", "--resume", run_dir.name],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert resumed.returncode == 0, resumed.stderr
    assert "Delivered 1 issue(s) in queue order." in resumed.stdout
    assert not run_dir.exists()


def test_issue_mode_uses_configured_claude_without_codex(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    config_dir = tmp_path / ".scratch"
    config_dir.mkdir()
    policy = {
        "version": 1,
        "readyLabel": "ready-for-agent",
        "branchPrefix": "agent/issue-",
        "ciTimeoutMinutes": 1,
        "primaryTimeoutMinutes": 60,
        "metadataTimeoutMinutes": 5,
        "maxPrimaryFixAttempts": 3,
        "localChecks": [{"name": "test", "command": "check", "arguments": ["ok"]}],
        "requiredChecks": ["test"],
    }
    (config_dir / "deliver-github-issues.json").write_text(json.dumps(policy), encoding="utf-8")
    fake_bin = tmp_path / "bin"
    install_fakes(fake_bin)
    (fake_bin / "codex.cmd").unlink(missing_ok=True)
    environment = isolated_agent_environment(fake_bin)
    environment["DGI_FAKE_STATE"] = str(tmp_path / "fake-state")

    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "deliver_github_issues.cli",
            "--issues",
            "#79",
            "--primary-agent",
            "claude",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "fake-state" / "calls.log").read_text(encoding="utf-8")
    assert "claude --print" in calls
    assert "codex exec" not in calls


def test_failures_use_fixed_exit_codes_and_preserve_state(tmp_path: Path) -> None:
    scenarios = [
        ("DGI_FAKE_DIRTY", 10),
        ("DGI_FAKE_NO_CHANGES", 20),
        ("DGI_FAKE_LOCAL_FAIL", 20),
        ("DGI_FAKE_CI_FAIL", 30),
        ("DGI_FAKE_CONFLICT", 30),
        ("DGI_FAKE_HEAD_DRIFT", 50),
        ("DGI_FAKE_PRIMARY_PUSH", 50),
        ("DGI_FAKE_AUDIT_PUSH", 50),
        ("DGI_FAKE_BAD_HANDOFF", 20),
        ("DGI_FAKE_BODY_DRIFT", 50),
    ]
    for index, (flag, expected_code) in enumerate(scenarios):
        repository = tmp_path / str(index)
        repository.mkdir()
        (repository / ".git").mkdir()
        config_dir = repository / ".scratch"
        config_dir.mkdir()
        policy = {
            "version": 1,
            "readyLabel": "ready-for-agent",
            "branchPrefix": "agent/issue-",
            "ciTimeoutMinutes": 1,
            "primaryTimeoutMinutes": 60,
            "metadataTimeoutMinutes": 5,
            "maxPrimaryFixAttempts": 3,
            "localChecks": [{"name": "test", "command": "check", "arguments": ["ok"]}],
            "requiredChecks": ["test"],
        }
        (config_dir / "deliver-github-issues.json").write_text(json.dumps(policy), encoding="utf-8")
        queue_path = repository / "queue.json"
        queue_path.write_text(
            json.dumps(
                {
                    "version": 1,
                    "repository": "meymchen/lspf",
                    "baseBranch": "main",
                    "issues": [{"number": 79, "skills": ["tdd"], "instruction": ""}],
                }
            ),
            encoding="utf-8",
        )
        fake_bin = repository / "bin"
        install_fakes(fake_bin)
        environment = isolated_agent_environment(fake_bin)
        environment["DGI_FAKE_STATE"] = str(repository / "fake-state")
        environment[flag] = "1"

        result = subprocess.run(
            [sys.executable, "-m", "deliver_github_issues.cli", "--queue", str(queue_path)],
            cwd=repository,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

        assert result.returncode == expected_code, (flag, result.stderr)
        runs = repository / ".agent-runs" / "deliver-github-issues"
        assert len(list(runs.iterdir())) == 1
        assert "Run state preserved at" in result.stderr
        if expected_code != 10 and flag != "DGI_FAKE_BODY_DRIFT":
            assert "Issue #79; phase=" in result.stderr
            assert "head=" in result.stderr
            assert "PR=" in result.stderr
        if flag == "DGI_FAKE_LOCAL_FAIL":
            run_dir = next(runs.iterdir())
            state = json.loads((run_dir / "state.json").read_text(encoding="utf-8"))
            assert state["current"]["localChecks"] == [
                {
                    "name": "test",
                    "command": "check ok",
                    "exitCode": 9,
                    "log": str(run_dir / "79-local-test.log"),
                }
            ]
            assert "Local check [failed]: check ok: exit 9" in result.stderr
            assert "exit=9" in (run_dir / "79-local-test.log").read_text(encoding="utf-8")


def test_final_sha_gate_failure_returns_to_primary_fix_loop(tmp_path: Path) -> None:
    (tmp_path / ".git").mkdir()
    config_dir = tmp_path / ".scratch"
    config_dir.mkdir()
    policy = {
        "version": 1,
        "readyLabel": "ready-for-agent",
        "branchPrefix": "agent/issue-",
        "ciTimeoutMinutes": 1,
        "primaryTimeoutMinutes": 60,
        "metadataTimeoutMinutes": 5,
        "maxPrimaryFixAttempts": 3,
        "localChecks": [{"name": "test", "command": "check", "arguments": ["ok"]}],
        "requiredChecks": ["test"],
    }
    (config_dir / "deliver-github-issues.json").write_text(json.dumps(policy), encoding="utf-8")
    queue = tmp_path / "queue.json"
    queue.write_text(
        json.dumps(
            {
                "version": 1,
                "repository": "meymchen/lspf",
                "baseBranch": "main",
                "issues": [{"number": 79, "skills": ["tdd"], "instruction": ""}],
            }
        ),
        encoding="utf-8",
    )
    fake_bin = tmp_path / "bin"
    install_fakes(fake_bin)
    environment = isolated_agent_environment(fake_bin)
    environment.update(
        {
            "DGI_FAKE_STATE": str(tmp_path / "fake-state"),
            "DGI_FAKE_FINAL_LOCAL_FAIL": "1",
            "DGI_FAKE_UNTRACKED": "1",
        }
    )

    result = subprocess.run(
        [sys.executable, "-m", "deliver_github_issues.cli", "--queue", str(queue)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    calls = (tmp_path / "fake-state" / "calls.log").read_text(encoding="utf-8")
    assert calls.count("codex exec --ephemeral --sandbox workspace-write") == 2
