param(
    [Parameter(Mandatory, ParameterSetName = 'New')][string]$Queue,
    [Parameter(Mandatory, ParameterSetName = 'Issues')][string]$Issues,
    [Parameter(Mandatory, ParameterSetName = 'Resume')][string]$Resume,
    [Parameter(ParameterSetName = 'Resume')][string]$Instruction = '',
    [Parameter(ParameterSetName = 'New')][Parameter(ParameterSetName = 'Issues')][string]$Config = '.github/deliver-github-issues.json',
    [switch]$WhatIf
)

$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'IssueQueue.psm1') -Force
$exitCodes = Get-IssueQueueExitCodes
$root = $null; $runsRoot = $null
$runDir = $null; $statePath = $null; $state = $null

function Stop-Queue([string]$Message, [int]$Code) {
    $exception = [InvalidOperationException]::new($Message)
    $exception.Data['ExitCode'] = $Code
    throw $exception
}
function Save-State { $state.updatedAt = [DateTimeOffset]::UtcNow.ToString('o'); Save-Json $state $statePath }
function Read-LiveIssue([int]$Number) {
    ConvertFrom-CommandJson (Invoke-LoggedCommand gh @('issue', 'view', [string]$Number, '--json', 'number,title,body,labels,updatedAt,state,comments,url') (Join-Path $runDir 'commands.log')) 'gh issue view'
}
function Write-StopSummary {
    if ($null -eq $state -or $null -eq $state.current) { return }
    [Console]::Error.WriteLine("Issue #$($state.current.number); phase=$($state.phase); head=$($state.current.testedSha); PR=$($state.current.prUrl)")
    if ($null -ne $state.current.audit) {
        foreach ($criterion in @($state.current.audit.criteria | Where-Object status -ne 'satisfied')) {
            [Console]::Error.WriteLine("Unchecked [$($criterion.status)]: $($criterion.text)")
        }
        foreach ($criterion in @($state.current.audit.criteria | Where-Object status -eq 'satisfied')) {
            $evidence = @($criterion.evidence | ForEach-Object { "$($_.kind)=$($_.value)" }) -join '; '
            [Console]::Error.WriteLine("Evidence [satisfied]: $($criterion.text): $evidence")
        }
    } else {
        foreach ($criterion in @($state.current.checkboxes | Where-Object { -not $_.checked })) {
            [Console]::Error.WriteLine("Unchecked [not audited]: $($criterion.text)")
        }
    }
}

try {
    $rootOutput = @(& git rev-parse --show-toplevel 2>$null)
    if ($LASTEXITCODE -ne 0 -or $rootOutput.Count -ne 1 -or -not $rootOutput[0]) { Stop-Queue 'Run this skill from inside a Git repository.' $exitCodes.Preflight }
    $root = [IO.Path]::GetFullPath(([string]$rootOutput[0]).Trim())
    $runsRoot = Join-Path $root '.agent-runs\deliver-github-issues'

    if ($PSCmdlet.ParameterSetName -in @('New', 'Issues')) {
        try {
            $configPath = if ([IO.Path]::IsPathRooted($Config)) { $Config } else { Join-Path $root $Config }
            $policy = Read-RepositoryPolicy $configPath
            if ($PSCmdlet.ParameterSetName -eq 'New') { $queueData = Read-IssueQueue $Queue }
            else { $queueData = Resolve-IssueSelection $Issues $policy.readyLabel $null }
        } catch { Stop-Queue $_.Exception.Message $exitCodes.Preflight }
        if ($WhatIf) { Get-Preview $queueData $policy | Write-Output; exit 0 }
        $runId = [DateTimeOffset]::UtcNow.ToString('yyyyMMddTHHmmssZ') + '-' + ([guid]::NewGuid().ToString('N').Substring(0, 8))
        $runDir = Join-Path $runsRoot $runId
        New-Item -ItemType Directory -Path $runDir -Force | Out-Null
        $statePath = Join-Path $runDir 'state.json'
        $state = [pscustomobject][ordered]@{ version = 1; runId = $runId; repository = $queueData.repository; baseBranch = $queueData.baseBranch; policy = $policy; issues = @($queueData.issues); index = 0; phase = 'preflight'; current = $null; updatedAt = '' }
        Save-State
        Assert-Preflight $queueData $policy $root (Join-Path $runDir 'preflight.log')
        $state.phase = 'prepare'; Save-State
    } else {
        $runDir = [IO.Path]::GetFullPath((Join-Path $runsRoot $Resume))
        $prefix = [IO.Path]::GetFullPath($runsRoot) + [IO.Path]::DirectorySeparatorChar
        if (-not $runDir.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { Stop-Queue 'Resume path escaped .agent-runs/deliver-github-issues.' $exitCodes.Preflight }
        $statePath = Join-Path $runDir 'state.json'
        if (-not (Test-Path -LiteralPath $statePath -PathType Leaf)) { Stop-Queue "Run state not found: $Resume" $exitCodes.Preflight }
        $state = Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json -Depth 30
        if ($state.version -ne 1 -or $state.runId -cne $Resume) { Stop-Queue 'Run state identity is invalid.' $exitCodes.Preflight }
        if ($state.phase -eq 'complete') { Stop-Queue 'Run is already complete.' $exitCodes.Preflight }
        $policy = $state.policy
        if ($null -ne $state.current -and $state.phase -ne 'prepare') {
            $actualBranch = (Invoke-LoggedCommand git @('branch', '--show-current') (Join-Path $runDir 'commands.log')).Output.Trim()
            if ($actualBranch -cne $state.current.branch) { Stop-Queue "Resume requires branch $($state.current.branch), got $actualBranch." $exitCodes.Drift }
        }
        if ($Instruction) { $state.issues[$state.index].instruction = $Instruction; Save-State }
    }

    while ($state.index -lt @($state.issues).Count) {
        $item = $state.issues[$state.index]; $number = [int]$item.number
        $branch = "$($policy.branchPrefix)$number"; $log = Join-Path $runDir 'commands.log'

        if ($state.phase -eq 'prepare') {
            $null = Invoke-LoggedCommand git @('fetch', '--prune', 'origin') $log
            $null = Invoke-LoggedCommand git @('switch', $state.baseBranch) $log
            $null = Invoke-LoggedCommand git @('merge', '--ff-only', "origin/$($state.baseBranch)") $log
            Assert-NewBranchAvailable $branch $log
            $null = Invoke-LoggedCommand git @('switch', '-c', $branch) $log
            $issue = Read-LiveIssue $number
            $state.current = [pscustomobject][ordered]@{
                number = $number; title = $issue.title; branch = $branch; issueUpdatedAt = $issue.updatedAt; issueUrl = $issue.url
                checkboxes = @(Get-Checkboxes $issue.body); testedSha = $null; implementation = $null
                localChecks = @(); ciChecks = @(); prNumber = $null; prUrl = $null; audit = $null
            }
            $state.phase = 'implement'; Save-State
        }

        if ($state.phase -in @('implement', 'needs_implementation')) {
            $implementationIssue = Read-LiveIssue $number
            if ($state.phase -eq 'needs_implementation') { $state.current.issueUpdatedAt = $implementationIssue.updatedAt; Save-State }
            try { $result = Invoke-AgentPhase 'implement' $policy $state $item $implementationIssue $runDir $root }
            catch { Stop-Queue $_.Exception.Message $exitCodes.Implementation }
            $required = @($item.skills)
            $missing = @($required | Where-Object { $_ -notin @($result.usedSkills) })
            if ($result.status -ne 'completed' -or $missing.Count) { Stop-Queue ("Implementation blocked; missing skills: " + ($missing -join ', ') + '; ' + (@($result.blockers) -join '; ')) $exitCodes.Implementation }
            if (-not (Invoke-LoggedCommand git @('status', '--porcelain') $log).Output) { Stop-Queue 'Implementation produced no changes.' $exitCodes.Implementation }
            $state.current.implementation = $result; $state.phase = 'local_gates'; Save-State
        }

        if ($state.phase -eq 'local_gates') {
            try { $state.current.localChecks = @(Invoke-LocalGates $state $policy $runDir) } catch { Stop-Queue $_.Exception.Message $exitCodes.Implementation }
            $metadata = Get-DeliveryMetadata $policy $state $runDir
            $state.current | Add-Member -NotePropertyName metadata -NotePropertyValue $metadata -Force
            $title = $metadata.commitTitle
            $null = Invoke-LoggedCommand git @('add', '--all') $log
            $null = Invoke-LoggedCommand git @('commit', '-m', $title) $log
            $state.current.testedSha = (Invoke-LoggedCommand git @('rev-parse', 'HEAD') $log).Output.Trim()
            $state.phase = 'publish'; Save-State
        }

        if ($state.phase -eq 'publish') {
            if ($null -eq $state.current.prNumber) {
                $null = Invoke-LoggedCommand git @('push', '--set-upstream', 'origin', $branch) $log
                $bodyPath = Join-Path $runDir "$number-pr-body.md"
                $tests = @($state.current.localChecks | ForEach-Object { "- ``$($_.command)``: exit $($_.exitCode)" }) -join "`n"
                "$($state.current.metadata.summary)`n`n## Verification`n`n$tests`n`nCloses #$number" | Set-Content -LiteralPath $bodyPath
                $title = $state.current.metadata.prTitle
                $url = (Invoke-LoggedCommand gh @('pr', 'create', '--title', $title, '--body-file', $bodyPath, '--base', $state.baseBranch, '--head', $branch) $log).Output.Trim()
                $pr = ConvertFrom-CommandJson (Invoke-LoggedCommand gh @('pr', 'view', $url, '--json', 'number,url,headRefOid') $log) 'gh pr view'
            } else {
                $null = Invoke-LoggedCommand git @('push', 'origin', $branch) $log
                $pr = ConvertFrom-CommandJson (Invoke-LoggedCommand gh @('pr', 'view', [string]$state.current.prNumber, '--json', 'number,url,headRefOid') $log) 'gh pr view'
            }
            if ($pr.headRefOid -ne $state.current.testedSha) { Stop-Queue 'PR head differs from tested commit.' $exitCodes.Drift }
            $state.current.prNumber = $pr.number; $state.current.prUrl = $pr.url; $state.phase = 'ci'; Save-State
        }

        if ($state.phase -eq 'ci') {
            try { $ci = Wait-PullRequestChecks $state.current.prNumber $state.current.testedSha $policy.requiredChecks $runDir $policy.ciTimeoutMinutes } catch {
                $code = if ($_.Exception.Data.Contains('ExitCode')) { [int]$_.Exception.Data['ExitCode'] } else { $exitCodes.CI }; Stop-Queue $_.Exception.Message $code
            }
            $state.current.ciChecks = @($ci.checks); $state.phase = 'audit'; Save-State
        }

        if ($state.phase -eq 'audit') {
            $issue = Read-LiveIssue $number
            if ($issue.updatedAt -cne $state.current.issueUpdatedAt) { Stop-Queue 'Issue changed after the implementation snapshot.' $exitCodes.Drift }
            try { $audit = Invoke-AgentPhase 'audit' $policy $state $item $issue $runDir $root }
            catch { Stop-Queue $_.Exception.Message $exitCodes.Acceptance }
            try { Assert-Audit $audit @($state.current.checkboxes) $state $root } catch { Stop-Queue $_.Exception.Message $exitCodes.Acceptance }
            $state.current.audit = $audit
            $updatedBody = Set-SatisfiedCheckboxes $issue.body $audit
            if ($updatedBody -cne $issue.body) {
                $bodyPath = Join-Path $runDir "$number-issue-body.md"; $updatedBody | Set-Content -LiteralPath $bodyPath
                if ((Read-LiveIssue $number).updatedAt -cne $state.current.issueUpdatedAt) { Stop-Queue 'Issue changed before checkbox update.' $exitCodes.Drift }
                $null = Invoke-LoggedCommand gh @('issue', 'edit', [string]$number, '--body-file', $bodyPath) $log
            }
            $commentPath = Join-Path $runDir "$number-audit-comment.md"
            $rows = @($audit.criteria | ForEach-Object { "- [$($_.status)] $($_.text): " + (@($_.evidence | ForEach-Object { "$($_.kind)=$($_.value)" }) -join '; ') }) -join "`n"
            "Acceptance audit for ``$($state.current.testedSha)`` in $($state.current.prUrl).`n`n$rows" | Set-Content -LiteralPath $commentPath
            $null = Invoke-LoggedCommand gh @('issue', 'comment', [string]$number, '--body-file', $commentPath) $log
            if (@($audit.criteria | Where-Object status -eq 'unsatisfied').Count) { $state.phase = 'needs_implementation'; Save-State; Stop-Queue 'Acceptance found implementation gaps; resume with an instruction to fix them.' $exitCodes.Acceptance }
            if (@($audit.criteria | Where-Object status -eq 'human_required').Count) { $state.phase = 'awaiting_human'; Save-State; Stop-Queue 'Human acceptance is required; check remaining boxes and comment /accept <head-sha>.' $exitCodes.Acceptance }
            $state.phase = 'merge'; Save-State
        }

        if ($state.phase -eq 'awaiting_human') {
            if (-not (Test-HumanAcceptance $state (Read-LiveIssue $number) $log)) { Stop-Queue 'Human acceptance is incomplete or does not match the tested SHA.' $exitCodes.Acceptance }
            try { $ci = Wait-PullRequestChecks $state.current.prNumber $state.current.testedSha $policy.requiredChecks $runDir 1 } catch { Stop-Queue $_.Exception.Message $exitCodes.CI }
            $state.current.ciChecks = @($ci.checks); $state.phase = 'merge'; Save-State
        }

        if ($state.phase -eq 'merge') {
            $pr = ConvertFrom-CommandJson (Invoke-LoggedCommand gh @('pr', 'view', [string]$state.current.prNumber, '--json', 'headRefOid,isDraft,mergeStateStatus,reviewDecision,state') $log) 'gh pr view'
            if ($pr.headRefOid -ne $state.current.testedSha) { Stop-Queue 'PR head drifted before merge.' $exitCodes.Drift }
            if ($pr.isDraft -or $pr.mergeStateStatus -eq 'DIRTY' -or $pr.reviewDecision -in @('CHANGES_REQUESTED', 'REVIEW_REQUIRED')) { Stop-Queue 'PR still requires human action.' $exitCodes.Acceptance }
            $title = $state.current.metadata.prTitle
            $null = Invoke-LoggedCommand gh @('pr', 'merge', [string]$state.current.prNumber, '--squash', '--match-head-commit', $state.current.testedSha, '--subject', $title) $log
            $merged = ConvertFrom-CommandJson (Invoke-LoggedCommand gh @('pr', 'view', [string]$state.current.prNumber, '--json', 'state,mergeCommit') $log) 'gh pr view'
            $closed = Read-LiveIssue $number
            if ($merged.state -ne 'MERGED' -or $closed.state -ne 'CLOSED') { Stop-Queue 'Merge or issue closure verification failed.' $exitCodes.Drift }
            $remote = Invoke-LoggedCommand git @('ls-remote', '--exit-code', '--heads', 'origin', "refs/heads/$branch") $log -AllowFailure
            if ($remote.ExitCode -eq 0) { $null = Invoke-LoggedCommand git @('push', 'origin', '--delete', $branch) $log }
            $null = Invoke-LoggedCommand git @('switch', $state.baseBranch) $log
            $null = Invoke-LoggedCommand git @('fetch', '--prune', 'origin') $log
            $null = Invoke-LoggedCommand git @('merge', '--ff-only', "origin/$($state.baseBranch)") $log
            if ((Invoke-LoggedCommand git @('merge-base', '--is-ancestor', $merged.mergeCommit.oid, 'HEAD') $log -AllowFailure).ExitCode -ne 0) { Stop-Queue 'Squash merge commit is not on local base branch.' $exitCodes.Drift }
            $null = Invoke-LoggedCommand git @('branch', '-D', $branch) $log
            if ((Invoke-LoggedCommand git @('status', '--porcelain') $log).Output) { Stop-Queue 'Working tree is not clean after cleanup.' $exitCodes.Drift }
            if ((Invoke-LoggedCommand git @('ls-remote', '--exit-code', '--heads', 'origin', "refs/heads/$branch") $log -AllowFailure).ExitCode -eq 0) { Stop-Queue 'Remote branch still exists after cleanup.' $exitCodes.Drift }
            if ((Invoke-LoggedCommand git @('show-ref', '--verify', '--quiet', "refs/remotes/origin/$branch") $log -AllowFailure).ExitCode -eq 0) { Stop-Queue 'Remote-tracking branch still exists after cleanup.' $exitCodes.Drift }
            $state.index++; $state.current = $null; $state.phase = if ($state.index -lt @($state.issues).Count) { 'prepare' } else { 'complete' }; Save-State
        }
    }

    $completed = [IO.Path]::GetFullPath($runDir); $prefix = [IO.Path]::GetFullPath($runsRoot) + [IO.Path]::DirectorySeparatorChar
    if (-not $completed.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase) -or (Split-Path $completed -Leaf) -cne $state.runId) { Stop-Queue 'Refusing to remove an invalid run directory.' $exitCodes.Drift }
    Remove-Item -LiteralPath $completed -Recurse -Force
    Write-Host "Delivered $($state.issues.Count) issue(s) in queue order."
    exit 0
} catch {
    $code = if ($_.Exception.Data.Contains('ExitCode')) {
        [int]$_.Exception.Data['ExitCode']
    } else {
        switch ($state.phase) {
            { $_ -in @('implement', 'needs_implementation', 'local_gates') } { $exitCodes.Implementation; break }
            'ci' { $exitCodes.CI; break }
            { $_ -in @('audit', 'awaiting_human') } { $exitCodes.Acceptance; break }
            { $_ -in @('publish', 'merge') } { $exitCodes.Drift; break }
            default { $exitCodes.Preflight }
        }
    }
    [Console]::Error.WriteLine($_.Exception.Message)
    Write-StopSummary
    if ($runDir) { [Console]::Error.WriteLine("Run state preserved at $runDir") }
    exit $code
}
