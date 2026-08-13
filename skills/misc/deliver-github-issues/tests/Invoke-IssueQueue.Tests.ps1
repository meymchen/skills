Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$script = Join-Path $PSScriptRoot '..\scripts\Invoke-IssueQueue.ps1'
$failures = [System.Collections.Generic.List[string]]::new()
$TestDrive = Join-Path ([IO.Path]::GetTempPath()) ("deliver-github-issues-tests-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $TestDrive | Out-Null

function Invoke-Test([string]$Name, [scriptblock]$Body) {
    if ($env:DGI_TEST_MATCH -and $Name -notlike "*$($env:DGI_TEST_MATCH)*") { return }
    try {
        & $Body
        Write-Host "PASS $Name"
    }
    catch {
        $failures.Add("${Name}: $($_.Exception.Message)")
        Write-Host "FAIL $Name"
    }
}

function Assert-Equal($Expected, $Actual, [string]$Message) {
    if ($Expected -ne $Actual) {
        throw "$Message (expected '$Expected', got '$Actual')"
    }
}

function New-FakeAdapters([string]$Directory) {
    New-Item -ItemType Directory -Path $Directory | Out-Null
    $fake = Join-Path $Directory 'fake.ps1'
    @'
param([string]$Tool, [Parameter(ValueFromRemainingArguments)][string[]]$Rest)
$log = $env:DGI_FAKE_LOG; Add-Content -LiteralPath $log -Value ($Tool + ' ' + ($Rest -join ' '))
$state = Split-Path $log -Parent
if ($Tool -eq 'codex') {
    if ($env:DGI_FAKE_FAIL_CODEX -eq '1') { exit 7 }
    $out = $Rest[[array]::IndexOf($Rest, '--output-last-message') + 1]
    $schema = $Rest[[array]::IndexOf($Rest, '--output-schema') + 1]
    if ($schema -like '*implement.schema.json') {
        if ($env:DGI_FAKE_NO_CHANGES -ne '1') { New-Item -ItemType File -Path (Join-Path $state 'changed') -Force | Out-Null }
        @{status='completed';summary='implemented';usedSkills=@('deliver-github-issues','implement','tdd');tests=@(@{command='targeted';exitCode=0});blockers=@()} | ConvertTo-Json -Depth 5 | Set-Content $out
    } else {
        if ($env:DGI_FAKE_UNSAT -eq '1') { @{summary='gap';criteria=@(@{index=0;text='works';status='unsatisfied';evidence=@()})} | ConvertTo-Json -Depth 5 | Set-Content $out }
        elseif ($env:DGI_FAKE_HUMAN -eq '1') { @{summary='human';criteria=@(@{index=0;text='works';status='human_required';evidence=@()})} | ConvertTo-Json -Depth 5 | Set-Content $out }
        else {
            $evidenceCommand = if ($env:DGI_FAKE_PORTABLE -eq '1') {'cargo portable'} else {'cargo fmt --all -- --check'}
            @{summary='accepted';criteria=@(@{index=0;text='works';status='satisfied';evidence=@(@{kind='command';value=$evidenceCommand})})} | ConvertTo-Json -Depth 5 | Set-Content $out
        }
    }
    '{"type":"result"}'; exit 0
}
if ($Tool -eq 'opencode') {
    '{"commitTitle":"cheap commit (#79)","prTitle":"cheap PR (#79)","summary":"cheap metadata summary"}'
    exit 0
}
if ($Tool -eq 'claude') {
    $joined = $Rest -join ' '
    if ($joined -match 'acceptEdits') {
        New-Item -ItemType File -Path (Join-Path $state 'changed') -Force | Out-Null
        @{structured_output=@{status='completed';summary='implemented by claude';usedSkills=@('implement');tests=@(@{command='targeted';exitCode=0});blockers=@()}} | ConvertTo-Json -Depth 8
    } else {
        @{structured_output=@{summary='accepted';criteria=@(@{index=0;text='works';status='satisfied';evidence=@(@{kind='command';value='cargo fmt --all -- --check'})})}} | ConvertTo-Json -Depth 8
    }
    exit 0
}
if ($Tool -in @('cargo','npx')) { exit 0 }
if ($Tool -eq 'git') {
    $argsText = $Rest -join ' '
    if ($argsText -eq 'rev-parse --show-toplevel') { if ($env:DGI_FAKE_REPO_ROOT) {$env:DGI_FAKE_REPO_ROOT} else {(Get-Location).Path}; exit 0 }
    if ($argsText -eq 'branch --show-current') { if (Test-Path (Join-Path $state 'on-branch')) {'codex/issue-79'} else {'main'}; exit 0 }
    if ($argsText -eq 'status --porcelain') { if ($env:DGI_FAKE_DIRTY -eq '1' -or ((Test-Path (Join-Path $state 'changed')) -and -not (Test-Path (Join-Path $state 'merged')))) { ' M file' }; exit 0 }
    if ($argsText -eq 'rev-parse --git-dir') { '.git'; exit 0 }
    if ($argsText -eq 'rev-parse HEAD') { if (Test-Path (Join-Path $state 'commit2')) {'dddddddddddddddddddddddddddddddddddddddd'} else {'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'}; exit 0 }
    if ($Rest[0] -eq 'remote') { 'https://github.com/meymchen/lspf.git'; exit 0 }
    if ($Rest[0] -eq 'show-ref') { if ($env:DGI_FAKE_BRANCH_CONFLICT -eq '1' -and $argsText -like '*refs/heads/*') { exit 0 }; exit 1 }
    if ($Rest[0] -eq 'commit') { if (Test-Path (Join-Path $state 'commit1')) { New-Item -ItemType File -Path (Join-Path $state 'commit2') -Force | Out-Null } else { New-Item -ItemType File -Path (Join-Path $state 'commit1') -Force | Out-Null }; exit 0 }
    if ($Rest[0] -eq 'switch' -and $Rest -contains '-c') { New-Item -ItemType File -Path (Join-Path $state 'on-branch') -Force | Out-Null; exit 0 }
    if ($argsText -eq 'switch main') { Remove-Item (Join-Path $state 'on-branch') -ErrorAction SilentlyContinue; exit 0 }
    if ($Rest[0] -eq 'ls-remote') { if (Test-Path (Join-Path $state 'remote')) { 'a refs/heads/codex/issue-79'; exit 0 }; exit 2 }
    if ($Rest[0] -eq 'push' -and $Rest -contains '--set-upstream') { New-Item -ItemType File -Path (Join-Path $state 'remote') -Force | Out-Null; exit 0 }
    if ($Rest[0] -eq 'push' -and $Rest -contains '--delete') { Remove-Item (Join-Path $state 'remote'); exit 0 }
    if ($Rest[0] -eq 'branch' -and $Rest -contains '-D') { New-Item -ItemType File -Path (Join-Path $state 'cleaned') -Force | Out-Null; exit 0 }
    exit 0
}
if ($Tool -eq 'gh') {
    $argsText = $Rest -join ' '
    if ($argsText -like 'repo view*') { '{"nameWithOwner":"meymchen/lspf","squashMergeAllowed":true,"defaultBranchRef":{"name":"main"}}'; exit 0 }
    if ($argsText -like 'issue view*') {
        $requested = [int]$Rest[[array]::IndexOf($Rest, 'view') + 1]
        if ($env:DGI_FAKE_DAG -eq '1') {
            $blockedBy = if ($env:DGI_FAKE_DAG_CYCLE -eq '1') {
                if ($requested -eq 14) {'[{"number":15,"state":"OPEN"}]'} elseif ($requested -eq 15) {'[{"number":14,"state":"OPEN"}]'} else {'[]'}
            } elseif ($requested -eq 14) {'[{"number":15,"state":"OPEN"}]'} else {'[]'}
            $label = if ($env:DGI_FAKE_NOT_READY -eq '1' -and $requested -eq 15) {'[]'} else {'[{"name":"ready-for-agent"}]'}
            $blockedCount = @($blockedBy | ConvertFrom-Json).Count
            '{"number":' + $requested + ',"title":"Issue ' + $requested + '","body":"- [ ] works","labels":' + $label + ',"updatedAt":"2026-01-01T00:00:00Z","state":"OPEN","comments":[],"url":"https://github.test/issues/' + $requested + '","blockedBy":{"nodes":' + $blockedBy + ',"totalCount":' + $blockedCount + '},"blocking":{"nodes":[],"totalCount":0}}'; exit 0
        }
        $countPath = Join-Path $state 'issue-count'; $count = if (Test-Path $countPath) {[int](Get-Content $countPath)} else {0}; $count++; $count | Set-Content $countPath
        $status = if (Test-Path (Join-Path $state 'merged')) {'CLOSED'} else {'OPEN'}
        $body = if ($env:DGI_FAKE_ACCEPT) {'- [x] works'} else {'- [ ] works'}
        $sha = if ($env:DGI_FAKE_ACCEPT -eq 'exact') {'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'} else {'cccccccccccccccccccccccccccccccccccccccc'}
        $comments = if ($env:DGI_FAKE_ACCEPT) {'[{"author":{"login":"operator"},"body":"/accept ' + $sha + '"}]'} else {'[]'}
        $updated = if ($env:DGI_FAKE_ISSUE_DRIFT -eq '1' -and $count -ge 4) {'2026-01-02T00:00:00Z'} else {'2026-01-01T00:00:00Z'}
        '{"number":79,"title":"Do thing","body":"' + $body + '","labels":[{"name":"ready-for-agent"}],"updatedAt":"' + $updated + '","state":"' + $status + '","comments":' + $comments + ',"url":"https://github.test/issues/79"}'; exit 0
    }
    if ($argsText -like 'pr list*') { '[]'; exit 0 }
    if ($argsText -like 'pr create*') { 'https://github.test/pr/1'; exit 0 }
    if ($argsText -like 'pr checks*') {
        if ($env:DGI_FAKE_CI_EMPTY -eq '1') { '[]'; exit 0 }
        $coverage = if ($env:DGI_FAKE_CI_FAIL -eq '1') {'fail'} else {'pass'}
        '[{"name":"markdownlint","bucket":"pass","state":"SUCCESS","link":"https://ci/1"},{"name":"fmt","bucket":"pass","state":"SUCCESS","link":"https://ci/2"},{"name":"clippy","bucket":"pass","state":"SUCCESS","link":"https://ci/3"},{"name":"test","bucket":"pass","state":"SUCCESS","link":"https://ci/4"},{"name":"coverage","bucket":"' + $coverage + '","state":"SUCCESS","link":"https://ci/5"}]'; exit 0
    }
    if ($argsText -like 'pr merge*') { New-Item -ItemType File -Path (Join-Path $state 'merged') -Force | Out-Null; exit 0 }
    if ($argsText -like 'pr view*') {
        $head = if ($env:DGI_FAKE_HEAD_DRIFT -eq '1') {'eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee'} elseif (Test-Path (Join-Path $state 'commit2')) {'dddddddddddddddddddddddddddddddddddddddd'} else {'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'}
        $mergeState = if ($env:DGI_FAKE_CONFLICT -eq '1') {'DIRTY'} else {'CLEAN'}
        if (Test-Path (Join-Path $state 'merged')) { '{"number":1,"url":"https://github.test/pr/1","headRefOid":"' + $head + '","isDraft":false,"mergeStateStatus":"' + $mergeState + '","reviewDecision":"APPROVED","state":"MERGED","mergeCommit":{"oid":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"}}' }
        else { '{"number":1,"url":"https://github.test/pr/1","headRefOid":"' + $head + '","isDraft":false,"mergeStateStatus":"' + $mergeState + '","reviewDecision":"APPROVED","state":"OPEN"}' }
        exit 0
    }
    if ($argsText -eq 'api user') { '{"login":"operator"}'; exit 0 }
    exit 0
}
exit 1
'@ | Set-Content -LiteralPath $fake
    foreach ($tool in @('git','gh','codex','claude','opencode','copilot','kimi','cargo','npx')) {
        if ($tool -in @('cargo','npx')) {
            "@echo off`r`necho $tool %*>>`"%DGI_FAKE_LOG%`"`r`nif `"%DGI_FAKE_LOCAL_FAIL%`"==`"1`" if `"%1`"==`"clippy`" exit /b 9`r`nexit /b 0" | Set-Content -LiteralPath (Join-Path $Directory "$tool.cmd")
        } else {
            "@echo off`r`npwsh -NoProfile -File `"$fake`" $tool %*" | Set-Content -LiteralPath (Join-Path $Directory "$tool.cmd")
        }
    }
}

Invoke-Test 'duplicate issues fail before external commands' {
    $queue = Join-Path $TestDrive 'duplicate.json'
    @{
        version = 1
        repository = 'meymchen/lspf'
        baseBranch = 'main'
        issues = @(
            @{ number = 79; skills = @('tdd'); instruction = '' },
            @{ number = 79; skills = @(); instruction = '' }
        )
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $queue

    & pwsh -NoProfile -File $script -Queue $queue 2>&1 | Out-Null
    Assert-Equal 10 $LASTEXITCODE 'duplicate issue exit code'
}

Invoke-Test 'WhatIf preserves queue order and creates no run state' {
    $queue = Join-Path $TestDrive 'ordered.json'
    @{
        version = 1
        repository = 'meymchen/lspf'
        baseBranch = 'main'
        issues = @(
            @{ number = 82; skills = @(); instruction = '' },
            @{ number = 79; skills = @('tdd'); instruction = 'test first' }
        )
    } | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $queue

    $runsPath = Join-Path (Get-Location) '.agent-runs\deliver-github-issues'
    $before = if (Test-Path $runsPath) { @(Get-ChildItem $runsPath -Directory).Count } else { 0 }
    $output = & pwsh -NoProfile -File $script -Queue $queue -WhatIf 2>&1 | Out-String
    Assert-Equal 0 $LASTEXITCODE 'WhatIf exit code'
    if ($output.IndexOf('#82') -ge $output.IndexOf('#79')) { throw 'queue order was not preserved' }
    $after = if (Test-Path $runsPath) { @(Get-ChildItem $runsPath -Directory).Count } else { 0 }
    Assert-Equal $before $after 'WhatIf created run state'
}

Invoke-Test 'external skill resolves policy from the target repository' {
    $target = Join-Path $TestDrive 'target-repository'
    $configDirectory = Join-Path $target '.github'
    New-Item -ItemType Directory -Path $configDirectory -Force | Out-Null
    $policy = Get-Content (Join-Path $PSScriptRoot '..\assets\repository.example.json') -Raw | ConvertFrom-Json -Depth 20
    $policy.branchPrefix = 'portable/issue-'
    $policy | ConvertTo-Json -Depth 20 | Set-Content (Join-Path $configDirectory 'deliver-github-issues.json')
    $queue = Join-Path $TestDrive 'external-skill.json'
    @{version=1;repository='meymchen/example';baseBranch='main';issues=@(@{number=79;skills=@();instruction=''})} | ConvertTo-Json -Depth 5 | Set-Content $queue
    $bin = Join-Path $TestDrive 'external-skill-bin'; New-FakeAdapters $bin
    $env:DGI_FAKE_LOG = Join-Path $bin 'calls.log'; $env:DGI_FAKE_REPO_ROOT = $target
    $oldPath = $env:PATH; $env:PATH = $bin + [IO.Path]::PathSeparator + $oldPath
    Push-Location $target
    try {
        $output = & pwsh -NoProfile -File $script -Queue $queue -WhatIf 2>&1 | Out-String
        Assert-Equal 0 $LASTEXITCODE "external skill WhatIf exit code; output=$output"
    } finally {
        Pop-Location
        $env:PATH = $oldPath
        Remove-Item Env:DGI_FAKE_REPO_ROOT
    }
    if ($output -notmatch 'portable/issue-79') { throw "target repository policy was not used: $output" }
}

Invoke-Test 'issue selectors expand ranges and DAG-sort with implement by default' {
    $bin = Join-Path $TestDrive 'selector-bin'; New-FakeAdapters $bin
    $env:DGI_FAKE_LOG = Join-Path $bin 'calls.log'; $env:DGI_FAKE_DAG = '1'
    $oldPath = $env:PATH; $env:PATH = $bin + [IO.Path]::PathSeparator + $oldPath
    try {
        $output = & pwsh -NoProfile -File $script -Issues '#14, #15-16' -WhatIf 2>&1 | Out-String
        Assert-Equal 0 $LASTEXITCODE "selector WhatIf exit code; output=$output"
    } finally { $env:PATH = $oldPath; Remove-Item Env:DGI_FAKE_DAG }
    if (-not ($output.IndexOf('#15') -lt $output.IndexOf('#14') -and $output.IndexOf('#14') -lt $output.IndexOf('#16'))) { throw "DAG order is wrong: $output" }
    if ($output -notmatch '#15.*skills=implement') { throw 'default implement skill is absent from preview' }
}

Invoke-Test 'every selected issue must be ready for agent' {
    $bin = Join-Path $TestDrive 'not-ready-bin'; New-FakeAdapters $bin
    $env:DGI_FAKE_LOG = Join-Path $bin 'calls.log'; $env:DGI_FAKE_DAG = '1'; $env:DGI_FAKE_NOT_READY = '1'
    $oldPath = $env:PATH; $env:PATH = $bin + [IO.Path]::PathSeparator + $oldPath
    try {
        & pwsh -NoProfile -File $script -Issues '#14-15' -WhatIf 2>&1 | Out-Null
        Assert-Equal 10 $LASTEXITCODE 'not-ready selector exit code'
    } finally {
        $env:PATH = $oldPath
        Remove-Item Env:DGI_FAKE_DAG
        Remove-Item Env:DGI_FAKE_NOT_READY
    }
}

Invoke-Test 'dependency cycles are rejected' {
    $bin = Join-Path $TestDrive 'cycle-bin'; New-FakeAdapters $bin
    $env:DGI_FAKE_LOG = Join-Path $bin 'calls.log'; $env:DGI_FAKE_DAG = '1'; $env:DGI_FAKE_DAG_CYCLE = '1'
    $oldPath = $env:PATH; $env:PATH = $bin + [IO.Path]::PathSeparator + $oldPath
    try {
        & pwsh -NoProfile -File $script -Issues '#14-15' -WhatIf 2>&1 | Out-Null
        Assert-Equal 10 $LASTEXITCODE 'cycle exit code'
    } finally {
        $env:PATH = $oldPath
        Remove-Item Env:DGI_FAKE_DAG
        Remove-Item Env:DGI_FAKE_DAG_CYCLE
    }
}

Invoke-Test 'repository policy makes checks branches and metadata agents portable' {
    $queue = Join-Path $TestDrive 'portable.json'
    @{version=1;repository='meymchen/lspf';baseBranch='main';issues=@(@{number=79;skills=@();instruction=''})} | ConvertTo-Json -Depth 5 | Set-Content $queue
    $config = Join-Path $TestDrive 'portable-policy.json'
    @{
        version=1;readyLabel='ready-for-agent';branchPrefix='agent/task-';ciTimeoutMinutes=1
        localChecks=@(@{name='portable-check';command='cargo';arguments=@('portable')})
        requiredChecks=@('test')
        primaryAgent=@{provider='codex';model=''}
        metadataAgent=@{provider='opencode';model='cheap/model';fallback=$false}
    } | ConvertTo-Json -Depth 8 | Set-Content $config
    $bin = Join-Path $TestDrive 'portable-bin'; New-FakeAdapters $bin
    $env:DGI_FAKE_LOG = Join-Path $bin 'calls.log'; $env:DGI_FAKE_PORTABLE = '1'
    $oldPath = $env:PATH; $env:PATH = $bin + [IO.Path]::PathSeparator + $oldPath
    try {
        $portableOutput = & pwsh -NoProfile -File $script -Queue $queue -Config $config 2>&1 | Out-String
        Assert-Equal 0 $LASTEXITCODE "portable policy exit code; output=$portableOutput"
    } finally { $env:PATH = $oldPath; Remove-Item Env:DGI_FAKE_PORTABLE }
    $calls = Get-Content $env:DGI_FAKE_LOG -Raw
    foreach ($needle in @('git switch -c agent/task-79', 'cargo portable', 'opencode run --model cheap/model', 'cheap commit (#79)')) {
        if ($calls -notmatch [regex]::Escape($needle)) { throw "portable policy missed: $needle" }
    }
}

Invoke-Test 'Claude and Codex entries are manual-only and Claude can run worker phases' {
    $codexMetadata = Get-Content (Join-Path $PSScriptRoot '..\agents\openai.yaml') -Raw
    if ($codexMetadata -notmatch 'allow_implicit_invocation:\s*false') { throw 'Codex entry allows model invocation' }
    $claudeSkill = Get-Content (Join-Path $PSScriptRoot '..\SKILL.md') -Raw
    if ($claudeSkill -notmatch 'disable-model-invocation:\s*true') { throw 'Claude entry allows model invocation' }
    $module = Get-Content (Join-Path $PSScriptRoot '..\scripts\IssueQueue.psm1') -Raw
    if ($module -match "skillCalls\s*=.*deliver-github-issues") { throw 'worker recursively invokes the manual-only skill' }

    $queue = Join-Path $TestDrive 'claude.json'
    @{version=1;repository='meymchen/lspf';baseBranch='main';issues=@(@{number=79;skills=@();instruction=''})} | ConvertTo-Json -Depth 5 | Set-Content $queue
    $config = Join-Path $TestDrive 'claude-policy.json'
    $policy = Get-Content (Join-Path $PSScriptRoot '..\assets\repository.example.json') -Raw | ConvertFrom-Json -Depth 20
    $policy.primaryAgent.provider = 'claude'; $policy.primaryAgent.model = 'sonnet'
    $policy.localChecks = @([pscustomobject]@{name='fmt';command='cargo';arguments=@('fmt','--all','--','--check')})
    $policy | ConvertTo-Json -Depth 20 | Set-Content $config
    $bin = Join-Path $TestDrive 'claude-bin'; New-FakeAdapters $bin
    $env:DGI_FAKE_LOG = Join-Path $bin 'calls.log'
    $oldPath = $env:PATH; $env:PATH = $bin + [IO.Path]::PathSeparator + $oldPath
    try { & pwsh -NoProfile -File $script -Queue $queue -Config $config 2>&1 | Out-Null; Assert-Equal 0 $LASTEXITCODE 'Claude worker exit code' }
    finally { $env:PATH = $oldPath }
    $calls = Get-Content $env:DGI_FAKE_LOG -Raw
    if ($calls -notmatch 'claude --print.*--model sonnet') { throw 'Claude worker was not selected' }
    if ($calls -match '(?m)^codex exec') { throw 'Codex worker ran under Claude policy' }
}

Invoke-Test 'happy path is strictly ordered and cleans its run state' {
    $queue = Join-Path $TestDrive 'happy.json'
    @{version=1;repository='meymchen/lspf';baseBranch='main';issues=@(@{number=79;skills=@('tdd');instruction=''})} | ConvertTo-Json -Depth 5 | Set-Content $queue
    $bin = Join-Path $TestDrive 'bin'; New-FakeAdapters $bin
    $env:DGI_FAKE_LOG = Join-Path $bin 'calls.log'
    $runsPath = Join-Path (Get-Location) '.agent-runs\deliver-github-issues'
    $before = if (Test-Path $runsPath) { @(Get-ChildItem $runsPath -Directory).Count } else { 0 }
    $oldPath = $env:PATH; $env:PATH = $bin + [IO.Path]::PathSeparator + $oldPath
    try { & pwsh -NoProfile -File $script -Queue $queue 2>&1 | Out-Null; Assert-Equal 0 $LASTEXITCODE 'happy path exit code' }
    finally { $env:PATH = $oldPath }
    $calls = Get-Content -LiteralPath $env:DGI_FAKE_LOG -Raw
    foreach ($needle in @('codex exec','cargo fmt','cargo clippy','cargo test','npx --yes','gh pr create','gh pr checks','gh pr merge','git push origin --delete codex/issue-79','git branch')) {
        if ($calls -notmatch [regex]::Escape($needle)) { throw "missing call: $needle" }
    }
    $after = if (Test-Path $runsPath) { @(Get-ChildItem $runsPath -Directory).Count } else { 0 }
    Assert-Equal $before $after 'successful run state was not removed'
}

Invoke-Test 'first implementation failure stops every later issue' {
    $queue = Join-Path $TestDrive 'fail-first.json'
    @{version=1;repository='meymchen/lspf';baseBranch='main';issues=@(
        @{number=79;skills=@('tdd');instruction=''}, @{number=80;skills=@();instruction=''}
    )} | ConvertTo-Json -Depth 5 | Set-Content $queue
    $bin = Join-Path $TestDrive 'fail-bin'; New-FakeAdapters $bin
    $env:DGI_FAKE_LOG = Join-Path $bin 'calls.log'; $env:DGI_FAKE_FAIL_CODEX = '1'
    $runsPath = Join-Path (Get-Location) '.agent-runs\deliver-github-issues'
    $legacyRunsPath = Join-Path (Get-Location) '.codex-runs'
    $beforeIds = if (Test-Path $runsPath) { @(Get-ChildItem $runsPath -Directory | ForEach-Object Name) } else { @() }
    $beforeLegacyIds = if (Test-Path $legacyRunsPath) { @(Get-ChildItem $legacyRunsPath -Directory | ForEach-Object Name) } else { @() }
    $oldPath = $env:PATH; $env:PATH = $bin + [IO.Path]::PathSeparator + $oldPath
    try { & pwsh -NoProfile -File $script -Queue $queue 2>&1 | Out-Null; Assert-Equal 20 $LASTEXITCODE 'implementation failure exit code' }
    finally { $env:PATH = $oldPath; Remove-Item Env:DGI_FAKE_FAIL_CODEX }
    $calls = Get-Content -LiteralPath $env:DGI_FAKE_LOG -Raw
    if ($calls -match 'codex/issue-80|pr (create|checks|merge).*80') { throw 'later issue received a delivery call' }
    Assert-Equal 1 ([regex]::Matches($calls, '(?m)^codex exec').Count) 'Codex was invoked after the first failure'
    $newRuns = @(Get-ChildItem -LiteralPath $runsPath -Directory | Where-Object Name -notin $beforeIds)
    Assert-Equal 1 $newRuns.Count 'failed run was not preserved'
    $afterLegacyIds = if (Test-Path $legacyRunsPath) { @(Get-ChildItem $legacyRunsPath -Directory | ForEach-Object Name) } else { @() }
    Assert-Equal ($beforeLegacyIds -join ',') ($afterLegacyIds -join ',') 'legacy .codex-runs received new state'
    Remove-Item -LiteralPath $newRuns[0].FullName -Recurse -Force
    if (@(Get-ChildItem -LiteralPath $runsPath -Force).Count -eq 0) { Remove-Item -LiteralPath $runsPath -Force }
}

Invoke-Test 'human gate requires checked boxes and the exact tested SHA' {
    $queue = Join-Path $TestDrive 'human.json'
    @{version=1;repository='meymchen/lspf';baseBranch='main';issues=@(@{number=79;skills=@('tdd');instruction=''})} | ConvertTo-Json -Depth 5 | Set-Content $queue
    $bin = Join-Path $TestDrive 'human-bin'; New-FakeAdapters $bin
    $env:DGI_FAKE_LOG = Join-Path $bin 'calls.log'; $env:DGI_FAKE_HUMAN = '1'
    $oldPath = $env:PATH; $env:PATH = $bin + [IO.Path]::PathSeparator + $oldPath
    try {
        & pwsh -NoProfile -File $script -Queue $queue 2>&1 | Out-Null
        Assert-Equal 40 $LASTEXITCODE 'human gate exit code'
        $run = Get-ChildItem (Join-Path (Get-Location) '.agent-runs\deliver-github-issues') -Directory | Select-Object -First 1
        $env:DGI_FAKE_ACCEPT = 'wrong'
        & pwsh -NoProfile -File $script -Resume $run.Name 2>&1 | Out-Null
        Assert-Equal 40 $LASTEXITCODE 'wrong SHA exit code'
        $env:DGI_FAKE_ACCEPT = 'exact'
        & pwsh -NoProfile -File $script -Resume $run.Name 2>&1 | Out-Null
        Assert-Equal 0 $LASTEXITCODE 'exact SHA resume exit code'
    } finally {
        $env:PATH = $oldPath
        Remove-Item Env:DGI_FAKE_HUMAN -ErrorAction SilentlyContinue
        Remove-Item Env:DGI_FAKE_ACCEPT -ErrorAction SilentlyContinue
    }
    $runsPath = Join-Path (Get-Location) '.agent-runs\deliver-github-issues'
    if ((Test-Path $runsPath) -and @(Get-ChildItem $runsPath -Directory).Count -gt 0) { throw 'accepted run state was not removed' }
}

Invoke-Test 'unsatisfied audit reuses the PR and invalidates the old head' {
    $queue = Join-Path $TestDrive 'unsatisfied.json'
    @{version=1;repository='meymchen/lspf';baseBranch='main';issues=@(@{number=79;skills=@('tdd');instruction=''})} | ConvertTo-Json -Depth 5 | Set-Content $queue
    $bin = Join-Path $TestDrive 'unsatisfied-bin'; New-FakeAdapters $bin
    $env:DGI_FAKE_LOG = Join-Path $bin 'calls.log'; $env:DGI_FAKE_UNSAT = '1'
    $oldPath = $env:PATH; $env:PATH = $bin + [IO.Path]::PathSeparator + $oldPath
    try {
        & pwsh -NoProfile -File $script -Queue $queue 2>&1 | Out-Null
        Assert-Equal 40 $LASTEXITCODE 'unsatisfied exit code'
        $run = Get-ChildItem (Join-Path (Get-Location) '.agent-runs\deliver-github-issues') -Directory | Select-Object -First 1
        Remove-Item Env:DGI_FAKE_UNSAT
        & pwsh -NoProfile -File $script -Resume $run.Name -Instruction 'fix gap' 2>&1 | Out-Null
        Assert-Equal 0 $LASTEXITCODE 'reimplementation exit code'
    } finally {
        $env:PATH = $oldPath
        Remove-Item Env:DGI_FAKE_UNSAT -ErrorAction SilentlyContinue
    }
    $calls = Get-Content -LiteralPath $env:DGI_FAKE_LOG -Raw
    Assert-Equal 1 ([regex]::Matches($calls, '(?m)^gh pr create').Count) 'a second PR was created'
    if ($calls -notmatch 'gh pr merge 1 --squash --match-head-commit d{40}') { throw 'merge did not use the new head SHA' }
}

foreach ($scenario in @(
    @{Name='dirty worktree'; Flag='DGI_FAKE_DIRTY'; Code=10},
    @{Name='branch conflict'; Flag='DGI_FAKE_BRANCH_CONFLICT'; Code=10},
    @{Name='no implementation changes'; Flag='DGI_FAKE_NO_CHANGES'; Code=20},
    @{Name='local gate failure'; Flag='DGI_FAKE_LOCAL_FAIL'; Code=20},
    @{Name='CI failure'; Flag='DGI_FAKE_CI_FAIL'; Code=30},
    @{Name='PR merge conflict'; Flag='DGI_FAKE_CONFLICT'; Code=30},
    @{Name='PR head drift'; Flag='DGI_FAKE_HEAD_DRIFT'; Code=50},
    @{Name='concurrent issue edit'; Flag='DGI_FAKE_ISSUE_DRIFT'; Code=50}
)) {
    Invoke-Test "$($scenario.Name) stops with its fixed exit code" {
        $queue = Join-Path $TestDrive ("scenario-" + $scenario.Flag + '.json')
        @{version=1;repository='meymchen/lspf';baseBranch='main';issues=@(@{number=79;skills=@('tdd');instruction=''})} | ConvertTo-Json -Depth 5 | Set-Content $queue
        $bin = Join-Path $TestDrive ("bin-" + $scenario.Flag); New-FakeAdapters $bin
        $env:DGI_FAKE_LOG = Join-Path $bin 'calls.log'
        [Environment]::SetEnvironmentVariable($scenario.Flag, '1', 'Process')
        $runsPath = Join-Path (Get-Location) '.agent-runs\deliver-github-issues'
        $beforeIds = if (Test-Path $runsPath) { @(Get-ChildItem $runsPath -Directory | ForEach-Object Name) } else { @() }
        $oldPath = $env:PATH; $env:PATH = $bin + [IO.Path]::PathSeparator + $oldPath
        try {
            & pwsh -NoProfile -File $script -Queue $queue 2>&1 | Out-Null
            Assert-Equal $scenario.Code $LASTEXITCODE "$($scenario.Name) exit code"
        } finally {
            $env:PATH = $oldPath
            [Environment]::SetEnvironmentVariable($scenario.Flag, $null, 'Process')
        }
        $newRuns = @(Get-ChildItem $runsPath -Directory | Where-Object Name -notin $beforeIds)
        Assert-Equal 1 $newRuns.Count "$($scenario.Name) did not preserve one run"
        Remove-Item -LiteralPath $newRuns[0].FullName -Recurse -Force
        if (@(Get-ChildItem $runsPath -Force).Count -eq 0) { Remove-Item -LiteralPath $runsPath -Force }
    }
}

Invoke-Test 'missing CI checks time out' {
    $bin = Join-Path $TestDrive 'timeout-bin'; New-FakeAdapters $bin
    $env:DGI_FAKE_LOG = Join-Path $bin 'calls.log'; $env:DGI_FAKE_CI_EMPTY = '1'
    $oldPath = $env:PATH; $env:PATH = $bin + [IO.Path]::PathSeparator + $oldPath
    try {
        Import-Module (Join-Path $PSScriptRoot '..\scripts\IssueQueue.psm1') -Force
        $message = $null
        try { $null = Wait-PullRequestChecks 1 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa' @('markdownlint', 'fmt', 'clippy', 'test', 'coverage') $TestDrive 0 }
        catch { $message = $_.Exception.Message }
        if ($message -notlike 'Timed out*') { throw "unexpected timeout result: $message" }
    } finally {
        $env:PATH = $oldPath
        Remove-Item Env:DGI_FAKE_CI_EMPTY
    }
}

if ($failures.Count -gt 0) {
    $failures | ForEach-Object { Write-Error $_ }
    Remove-Item -LiteralPath $TestDrive -Recurse -Force
    exit 1
}
Remove-Item -LiteralPath $TestDrive -Recurse -Force
