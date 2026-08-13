Set-StrictMode -Version Latest

$script:ExitCodes = @{ Preflight = 10; Implementation = 20; CI = 30; Acceptance = 40; Drift = 50 }

function New-QueueException([string]$Message, [int]$ExitCode) {
    $exception = [InvalidOperationException]::new($Message)
    $exception.Data['ExitCode'] = $ExitCode
    return $exception
}

function Read-IssueQueue {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Queue file does not exist: $Path" }
    try { $queue = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json -Depth 20 }
    catch { throw "Queue is not valid JSON: $($_.Exception.Message)" }
    $names = @($queue.PSObject.Properties.Name)
    $allowed = @('version', 'repository', 'baseBranch', 'issues')
    foreach ($name in $names) { if ($name -notin $allowed) { throw "Unknown queue property: $name" } }
    foreach ($required in $allowed) { if ($required -notin $names) { throw "Queue is missing '$required'." } }
    if ($queue.version -ne 1) { throw 'Queue version must be 1.' }
    if ($queue.repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') { throw 'Queue repository must be an owner/name pair.' }
    if ([string]::IsNullOrWhiteSpace($queue.baseBranch)) { throw 'Queue baseBranch is required.' }
    if (@($queue.issues).Count -eq 0) { throw 'Queue must contain at least one issue.' }
    $seen = @{}
    foreach ($issue in @($queue.issues)) {
        $issueNames = @($issue.PSObject.Properties.Name)
        $issueAllowed = @('number', 'skills', 'instruction')
        foreach ($name in $issueNames) { if ($name -notin $issueAllowed) { throw "Unknown issue property: $name" } }
        foreach ($required in $issueAllowed) { if ($required -notin $issueNames) { throw "Issue entry is missing '$required'." } }
        if ($issue.number -isnot [long] -or $issue.number -lt 1) { throw 'Issue number must be a positive integer.' }
        if ($seen.ContainsKey([string]$issue.number)) { throw "Duplicate issue number: $($issue.number)" }
        $seen[[string]$issue.number] = $true
        $skillSeen = @{}
        foreach ($skill in @($issue.skills)) {
            if ($skill -isnot [string] -or $skill -notmatch '^[a-z0-9][a-z0-9-]*$') { throw "Invalid skill name: $skill" }
            if ($skillSeen.ContainsKey($skill)) { throw "Duplicate skill for issue $($issue.number): $skill" }
            $skillSeen[$skill] = $true
        }
        $issue.skills = @('implement') + @($issue.skills | Where-Object { $_ -cne 'implement' })
        if ($issue.instruction -isnot [string]) { throw 'Issue instruction must be a string.' }
    }
    return $queue
}

function Read-RepositoryPolicy([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Repository delivery config does not exist: $Path" }
    try { $policy = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json -Depth 20 }
    catch { throw "Repository delivery config is not valid JSON: $($_.Exception.Message)" }
    $allowed = @('version', 'readyLabel', 'branchPrefix', 'ciTimeoutMinutes', 'localChecks', 'requiredChecks', 'primaryAgent', 'metadataAgent')
    foreach ($name in @($policy.PSObject.Properties.Name)) { if ($name -notin $allowed) { throw "Unknown repository config property: $name" } }
    foreach ($name in $allowed) { if ($name -notin @($policy.PSObject.Properties.Name)) { throw "Repository config is missing '$name'." } }
    if ($policy.version -ne 1) { throw 'Repository config version must be 1.' }
    if ($policy.readyLabel -isnot [string] -or [string]::IsNullOrWhiteSpace($policy.readyLabel)) { throw 'readyLabel is required.' }
    if ($policy.branchPrefix -notmatch '^[A-Za-z0-9._/-]+$') { throw 'branchPrefix is invalid.' }
    if ($policy.ciTimeoutMinutes -isnot [long] -or $policy.ciTimeoutMinutes -lt 1 -or $policy.ciTimeoutMinutes -gt 1440) { throw 'ciTimeoutMinutes must be between 1 and 1440.' }
    if (@($policy.localChecks).Count -eq 0 -or @($policy.requiredChecks).Count -eq 0) { throw 'At least one local check and one required CI check are required.' }
    if ($policy.primaryAgent.provider -notin @('codex', 'claude')) { throw "Unsupported primary agent: $($policy.primaryAgent.provider)" }
    $names = @{}
    foreach ($check in @($policy.localChecks)) {
        foreach ($field in @('name', 'command', 'arguments')) { if ($field -notin @($check.PSObject.Properties.Name)) { throw "Local check is missing '$field'." } }
        if ($names.ContainsKey($check.name)) { throw "Duplicate local check: $($check.name)" }; $names[$check.name] = $true
        if ([string]::IsNullOrWhiteSpace($check.name) -or [string]::IsNullOrWhiteSpace($check.command)) { throw 'Local check name and command are required.' }
    }
    $providers = @('deterministic', 'codex', 'opencode', 'copilot', 'kimi')
    if ($policy.metadataAgent.provider -notin $providers) { throw "Unsupported metadata agent: $($policy.metadataAgent.provider)" }
    if ($policy.metadataAgent.provider -eq 'copilot' -and $policy.metadataAgent.model) { throw 'Copilot CLI does not expose non-interactive model selection; configure its default model and leave metadataAgent.model empty.' }
    if ($policy.metadataAgent.fallback -isnot [bool]) { throw 'metadataAgent.fallback must be boolean.' }
    return $policy
}

function ConvertFrom-IssueSelector([string]$Selector) {
    if ([string]::IsNullOrWhiteSpace($Selector)) { throw 'Issue selector is required.' }
    $numbers = [System.Collections.Generic.List[int]]::new()
    $seen = @{}
    foreach ($part in @($Selector -split ',')) {
        if ($part.Trim() -notmatch '^#?(?<start>\d+)(?:\s*-\s*#?(?<end>\d+))?$') { throw "Invalid issue selector segment: $($part.Trim())" }
        $start = [int]$Matches['start']; $end = if ($Matches['end']) { [int]$Matches['end'] } else { $start }
        if ($start -lt 1 -or $end -lt $start) { throw "Invalid issue range: $($part.Trim())" }
        if (($end - $start + 1) -gt 500) { throw "Issue range is too large: $($part.Trim())" }
        for ($number = $start; $number -le $end; $number++) {
            if (-not $seen.ContainsKey($number)) { $numbers.Add($number); $seen[$number] = $true }
        }
    }
    return $numbers.ToArray()
}

function Resolve-IssueSelection([string]$Selector, [string]$ReadyLabel, [string]$LogPath) {
    foreach ($command in @('git', 'gh', 'codex')) {
        if (-not (Get-Command $command -ErrorAction SilentlyContinue)) { throw "Required command is unavailable: $command" }
    }
    $null = Invoke-LoggedCommand gh @('auth', 'status') $LogPath
    $repository = ConvertFrom-CommandJson (Invoke-LoggedCommand gh @('repo', 'view', '--json', 'nameWithOwner,defaultBranchRef') $LogPath) 'gh repo view'
    $numbers = @(ConvertFrom-IssueSelector $Selector)
    $selected = @{}; $order = @{}; $issues = @{}
    for ($i = 0; $i -lt $numbers.Count; $i++) { $selected[[int]$numbers[$i]] = $true; $order[[int]$numbers[$i]] = $i }
    foreach ($number in $numbers) {
        $issue = ConvertFrom-CommandJson (Invoke-LoggedCommand gh @('issue', 'view', [string]$number, '--repo', $repository.nameWithOwner, '--json', 'number,title,state,labels,blockedBy,blocking') $LogPath) 'gh issue view'
        if ($issue.state -ne 'OPEN') { throw "Issue #$number is not open." }
        if ($ReadyLabel -notin @($issue.labels | ForEach-Object { $_.name })) { throw "Issue #$number lacks $ReadyLabel." }
        foreach ($relationship in @('blockedBy', 'blocking')) {
            if (@($issue.$relationship.nodes).Count -ne $issue.$relationship.totalCount) { throw "Issue #$number has a truncated $relationship relationship." }
        }
        $issues[[int]$number] = $issue
    }
    $adjacent = @{}; $indegree = @{}; foreach ($number in $numbers) { $adjacent[[int]$number] = [System.Collections.Generic.HashSet[int]]::new(); $indegree[[int]$number] = 0 }
    function Add-Edge([int]$From, [int]$To) { if ($adjacent[$From].Add($To)) { $indegree[$To]++ } }
    foreach ($number in $numbers) {
        foreach ($dependency in @($issues[[int]$number].blockedBy.nodes)) {
            if ($selected.ContainsKey([int]$dependency.number)) { Add-Edge ([int]$dependency.number) ([int]$number) }
            elseif ($dependency.state -eq 'OPEN') { throw "Issue #$number is blocked by open issue #$($dependency.number), which is not selected." }
        }
        foreach ($dependent in @($issues[[int]$number].blocking.nodes)) {
            if ($selected.ContainsKey([int]$dependent.number)) { Add-Edge ([int]$number) ([int]$dependent.number) }
        }
    }
    $result = [System.Collections.Generic.List[int]]::new()
    while ($result.Count -lt $numbers.Count) {
        $next = @($numbers | Where-Object { $_ -notin $result -and $indegree[[int]$_] -eq 0 } | Sort-Object { $order[[int]$_] } | Select-Object -First 1)
        if ($next.Count -eq 0) { throw 'Selected issues contain a dependency cycle.' }
        $current = [int]$next[0]; $result.Add($current)
        foreach ($dependent in $adjacent[$current]) { $indegree[$dependent]-- }
    }
    return [pscustomobject][ordered]@{
        version = 1; repository = $repository.nameWithOwner; baseBranch = $repository.defaultBranchRef.name
        issues = @($result | ForEach-Object { [pscustomobject][ordered]@{ number = $_; skills = @('implement'); instruction = '' } })
    }
}

function Save-Json([object]$Value, [string]$Path) {
    $temporary = "$Path.tmp"
    $Value | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $temporary -Encoding utf8
    Move-Item -LiteralPath $temporary -Destination $Path -Force
}

function Invoke-LoggedCommand {
    param(
        [Parameter(Mandatory)][string]$Command,
        [string[]]$Arguments = @(),
        [string]$LogPath,
        [switch]$AllowFailure
    )
    $rendered = $Command + ' ' + (($Arguments | ForEach-Object { if ($_ -match '\s') { '"' + $_ + '"' } else { $_ } }) -join ' ')
    if ($LogPath) { Add-Content -LiteralPath $LogPath -Value "`n> $rendered" }
    $output = @(& $Command @Arguments 2>&1)
    $code = $LASTEXITCODE
    if ($null -eq $code) { $code = 0 }
    if ($LogPath -and $output.Count) { $output | ForEach-Object { Add-Content -LiteralPath $LogPath -Value ([string]$_) } }
    if ($code -ne 0 -and -not $AllowFailure) { throw "Command failed ($code): $rendered" }
    return [pscustomobject]@{ Output = (($output | ForEach-Object { [string]$_ }) -join "`n"); ExitCode = $code; CommandLine = $rendered }
}

function ConvertFrom-CommandJson([object]$Result, [string]$Description) {
    try { return $Result.Output | ConvertFrom-Json -Depth 30 }
    catch { throw "$Description returned invalid JSON: $($_.Exception.Message)" }
}

function Get-Checkboxes([string]$Body) {
    $matches = [regex]::Matches($Body, '(?m)^(?<prefix>\s*[-*]\s+)\[(?<mark>[ xX])\](?<suffix>\s+)(?<text>.+?)\s*$')
    $items = @()
    for ($i = 0; $i -lt $matches.Count; $i++) {
        $items += [pscustomobject]@{ index = $i; text = $matches[$i].Groups['text'].Value; checked = $matches[$i].Groups['mark'].Value -match '[xX]' }
    }
    return $items
}

function Assert-Preflight([object]$Queue, [object]$Policy, [string]$Root, [string]$LogPath) {
    $commands = @($Policy.primaryAgent.provider, 'git', 'gh') + @($Policy.localChecks | ForEach-Object { $_.command })
    if ($Policy.metadataAgent.provider -ne 'deterministic' -and -not $Policy.metadataAgent.fallback) {
        $commands += switch ($Policy.metadataAgent.provider) { 'copilot' { 'copilot' }; 'kimi' { 'kimi' }; default { $Policy.metadataAgent.provider } }
    }
    foreach ($command in @($commands | Select-Object -Unique)) {
        if (-not (Get-Command $command -ErrorAction SilentlyContinue)) { throw "Required command is unavailable: $command" }
    }
    $null = Invoke-LoggedCommand gh @('auth', 'status') $LogPath
    $repo = ConvertFrom-CommandJson (Invoke-LoggedCommand gh @('repo', 'view', '--json', 'nameWithOwner,squashMergeAllowed') $LogPath) 'gh repo view'
    if ($repo.nameWithOwner -cne $Queue.repository) { throw "Repository mismatch: expected $($Queue.repository), got $($repo.nameWithOwner)" }
    if (-not $repo.squashMergeAllowed) { throw 'Repository does not allow squash merges.' }
    $branch = (Invoke-LoggedCommand git @('branch', '--show-current') $LogPath).Output.Trim()
    if ($branch -cne $Queue.baseBranch) { throw "Current branch must be $($Queue.baseBranch)." }
    if ((Invoke-LoggedCommand git @('status', '--porcelain') $LogPath).Output) { throw 'Working tree must be clean.' }
    $gitDir = (Invoke-LoggedCommand git @('rev-parse', '--git-dir') $LogPath).Output.Trim()
    if (-not [IO.Path]::IsPathRooted($gitDir)) { $gitDir = Join-Path $Root $gitDir }
    foreach ($marker in @('MERGE_HEAD', 'rebase-merge', 'rebase-apply')) {
        if (Test-Path -LiteralPath (Join-Path $gitDir $marker)) { throw 'A merge or rebase is in progress.' }
    }
    $remote = (Invoke-LoggedCommand git @('remote', 'get-url', 'origin') $LogPath).Output.Trim()
    $remoteRepo = $remote -replace '^https://github\.com/', '' -replace '^git@github\.com:', '' -replace '\.git$', ''
    if ($remoteRepo -cne $Queue.repository) { throw "origin mismatch: expected $($Queue.repository), got $remoteRepo" }
    foreach ($item in @($Queue.issues)) {
        $issue = ConvertFrom-CommandJson (Invoke-LoggedCommand gh @('issue', 'view', [string]$item.number, '--json', 'state,labels') $LogPath) 'gh issue view'
        if ($issue.state -ne 'OPEN') { throw "Issue #$($item.number) is not open." }
        if ($Policy.readyLabel -notin @($issue.labels | ForEach-Object { $_.name })) { throw "Issue #$($item.number) lacks $($Policy.readyLabel)." }
    }
}

function Assert-NewBranchAvailable([string]$Branch, [string]$LogPath) {
    $local = Invoke-LoggedCommand git @('show-ref', '--verify', '--quiet', "refs/heads/$Branch") $LogPath -AllowFailure
    if ($local.ExitCode -eq 0) { throw "Local branch already exists: $Branch" }
    $remote = Invoke-LoggedCommand git @('ls-remote', '--exit-code', '--heads', 'origin', "refs/heads/$Branch") $LogPath -AllowFailure
    if ($remote.ExitCode -eq 0) { throw "Remote branch already exists: $Branch" }
    $prs = ConvertFrom-CommandJson (Invoke-LoggedCommand gh @('pr', 'list', '--state', 'all', '--head', $Branch, '--json', 'number') $LogPath) 'gh pr list'
    if (@($prs).Count -gt 0) { throw "A PR already exists for $Branch." }
}

function Invoke-AgentPhase {
    param([string]$Phase, [object]$Policy, [object]$State, [object]$QueueItem, [object]$Issue, [string]$RunDir, [string]$Root)
    $schema = Join-Path $PSScriptRoot "$Phase.schema.json"
    $promptPath = Join-Path $RunDir "$($State.current.number)-$Phase-prompt.txt"
    $resultPath = Join-Path $RunDir "$($State.current.number)-$Phase-result.json"
    $eventsPath = Join-Path $RunDir "$($State.current.number)-$Phase-events.jsonl"
    $skillCalls = @($QueueItem.skills | ForEach-Object { '$' + $_ })
    $payload = [ordered]@{
        phase = $Phase
        skills = $skillCalls
        instruction = $QueueItem.instruction
        headSha = $State.current.testedSha
        issue = $Issue
        originalCheckboxes = $State.current.checkboxes
        localChecks = $State.current.localChecks
        ciChecks = $State.current.ciChecks
    } | ConvertTo-Json -Depth 30
    ("This is a worker phase of a delivery workflow the user already invoked manually. Do not invoke the manual-only deliver-github-issues skill. Invoke required implementation skills in this order: " + ($skillCalls -join ', ') + ". You may also invoke any other installed and enabled skill relevant to the issue; report every skill actually used. Ignore unavailable or disabled optional skills unless the issue explicitly requires one. Follow the $Phase contract: implement may edit the workspace and run targeted tests; audit must keep it read-only and classify every supplied checkbox. Return only the requested schema object.`nInput:`n" + $payload) | Set-Content -LiteralPath $promptPath -Encoding utf8
    $sandbox = if ($Phase -eq 'audit') { 'read-only' } else { 'workspace-write' }
    if ($Policy.primaryAgent.provider -eq 'codex') {
        $arguments = @('exec', '--ephemeral', '--sandbox', $sandbox, '--json', '--output-schema', $schema, '--output-last-message', $resultPath, '-C', $Root)
        if ($Policy.primaryAgent.model) { $arguments += @('--model', [string]$Policy.primaryAgent.model) }; $arguments += '-'
        $events = @(Get-Content -LiteralPath $promptPath -Raw | & codex @arguments 2>&1)
        $code = $LASTEXITCODE; $events | Set-Content -LiteralPath $eventsPath -Encoding utf8
        if ($code -ne 0) { throw "Codex $Phase failed with exit code $code." }
    } else {
        $schemaText = Get-Content -LiteralPath $schema -Raw | ConvertFrom-Json -Depth 30 | ConvertTo-Json -Depth 30 -Compress
        $permissionMode = if ($Phase -eq 'audit') { 'plan' } else { 'acceptEdits' }
        $allowedTools = if ($Phase -eq 'audit') { 'Read,Glob,Grep' } else { 'Read,Edit,Write,Glob,Grep,Bash' }
        $arguments = @('--print', '--no-session-persistence', '--output-format', 'json', '--json-schema', $schemaText, '--permission-mode', $permissionMode, '--allowedTools', $allowedTools)
        if ($Policy.primaryAgent.model) { $arguments += @('--model', [string]$Policy.primaryAgent.model) }
        $events = @(Get-Content -LiteralPath $promptPath -Raw | & claude @arguments 2>&1)
        $code = $LASTEXITCODE; $events | Set-Content -LiteralPath $eventsPath -Encoding utf8
        if ($code -ne 0) { throw "Claude $Phase failed with exit code $code." }
        try {
            $envelope = (($events | ForEach-Object { [string]$_ }) -join "`n") | ConvertFrom-Json -Depth 30
            $structured = if ($null -ne $envelope.structured_output) { $envelope.structured_output } elseif ($envelope.result -is [string]) { $envelope.result | ConvertFrom-Json -Depth 30 } else { $envelope }
            $structured | ConvertTo-Json -Depth 30 | Set-Content -LiteralPath $resultPath -Encoding utf8
        } catch { throw "Claude $Phase produced invalid structured JSON: $($_.Exception.Message)" }
    }
    if (-not (Test-Path -LiteralPath $resultPath)) { throw "$($Policy.primaryAgent.provider) $Phase produced no structured result." }
    try { return Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json -Depth 30 }
    catch { throw "Codex $Phase result is invalid JSON: $($_.Exception.Message)" }
}

function Invoke-LocalGates([object]$State, [object]$Policy, [string]$RunDir) {
    $results = @()
    foreach ($check in @($Policy.localChecks)) {
        $log = Join-Path $RunDir "$($State.current.number)-local-$($check.Name).log"
        $result = Invoke-LoggedCommand $check.Command $check.Arguments $log -AllowFailure
        $results += [pscustomobject]@{ name = $check.Name; command = $result.CommandLine; exitCode = $result.ExitCode; log = $log }
        if ($result.ExitCode -ne 0) { throw "Local check failed: $($check.Name)" }
    }
    return $results
}

function Get-DeterministicMetadata([object]$State) {
    $title = ($State.current.title -replace '[\r\n]+', ' ').Trim() + " (#$($State.current.number))"
    return [pscustomobject]@{ commitTitle = $title; prTitle = $title; summary = [string]$State.current.implementation.summary }
}

function ConvertFrom-MetadataOutput([string]$Output) {
    $candidates = [System.Collections.Generic.List[string]]::new()
    if (-not [string]::IsNullOrWhiteSpace($Output)) { $candidates.Add($Output.Trim()) }
    foreach ($line in @($Output -split "`r?`n" | Select-Object -Last 100 | Sort-Object { 0 })) {
        if ($line.Trim().StartsWith('{')) { $candidates.Add($line.Trim()) }
        try {
            $event = $line | ConvertFrom-Json -Depth 20 -ErrorAction Stop
            foreach ($property in @('text', 'content', 'message', 'result', 'output')) {
                if ($event.PSObject.Properties.Name -contains $property -and $event.$property -is [string]) { $candidates.Add($event.$property) }
            }
        } catch { }
    }
    if ($Output -match '(?s)(\{\s*"commitTitle".+"summary"\s*:\s*".*?"\s*\})') { $candidates.Add($Matches[1]) }
    $candidateArray = @($candidates); [array]::Reverse($candidateArray)
    foreach ($candidate in $candidateArray) {
        try {
            $value = $candidate | ConvertFrom-Json -Depth 20 -ErrorAction Stop
            if ($value.commitTitle -is [string] -and $value.prTitle -is [string] -and $value.summary -is [string]) { return $value }
        } catch { }
    }
    throw 'Metadata agent returned no valid metadata JSON object.'
}

function Assert-DeliveryMetadata([object]$Metadata, [int]$IssueNumber) {
    foreach ($field in @('commitTitle', 'prTitle')) {
        $value = [string]$Metadata.$field
        if ([string]::IsNullOrWhiteSpace($value) -or $value.Length -gt 200 -or $value -match '[\r\n]') { throw "Metadata $field is invalid." }
        if ($value -notmatch "\(#$IssueNumber\)$") { throw "Metadata $field must end with (#$IssueNumber)." }
    }
    if ([string]::IsNullOrWhiteSpace($Metadata.summary) -or ([string]$Metadata.summary).Length -gt 4000) { throw 'Metadata summary is invalid.' }
}

function Get-DeliveryMetadata([object]$Policy, [object]$State, [string]$RunDir) {
    $fallback = Get-DeterministicMetadata $State
    $agent = $Policy.metadataAgent
    if ($agent.provider -eq 'deterministic') { return $fallback }
    $temporary = Join-Path ([IO.Path]::GetTempPath()) ("deliver-metadata-" + [guid]::NewGuid().ToString('N'))
    New-Item -ItemType Directory -Path $temporary | Out-Null
    try {
        $prompt = @"
Return only compact JSON with string fields commitTitle, prTitle, and summary.
Both titles must end with (#$($State.current.number)), contain no newline, and be at most 200 characters.
Write a concise factual summary. Do not use tools or inspect files.
Issue title: $($State.current.title)
Implementation summary: $($State.current.implementation.summary)
Successful checks: $(@($State.current.localChecks | ForEach-Object command) -join '; ')
"@
        $model = [string]$agent.model
        Push-Location $temporary
        try {
            switch ($agent.provider) {
                'codex' {
                    $schema = Join-Path $PSScriptRoot 'metadata.schema.json'; $resultPath = Join-Path $temporary 'result.json'
                    $arguments = @('exec', '--ephemeral', '--sandbox', 'workspace-write', '--skip-git-repo-check', '--output-schema', $schema, '--output-last-message', $resultPath)
                    if ($model) { $arguments += @('--model', $model) }; $arguments += $prompt
                    $result = Invoke-LoggedCommand codex $arguments (Join-Path $RunDir 'metadata-agent.log') -AllowFailure
                    if ($result.ExitCode -ne 0 -or -not (Test-Path $resultPath)) { throw "codex metadata agent failed ($($result.ExitCode))." }
                    $metadata = Get-Content $resultPath -Raw | ConvertFrom-Json -Depth 20
                }
                'opencode' {
                    $arguments = @('run'); if ($model) { $arguments += @('--model', $model) }; $arguments += $prompt
                    $result = Invoke-LoggedCommand opencode $arguments (Join-Path $RunDir 'metadata-agent.log') -AllowFailure
                    if ($result.ExitCode -ne 0) { throw "opencode metadata agent failed ($($result.ExitCode))." }; $metadata = ConvertFrom-MetadataOutput $result.Output
                }
                'copilot' {
                    $arguments = @('--prompt', $prompt, '--stream', 'off', '--sandbox', 'on', '--deny-tool', '*')
                    $result = Invoke-LoggedCommand copilot $arguments (Join-Path $RunDir 'metadata-agent.log') -AllowFailure
                    if ($result.ExitCode -ne 0) { throw "copilot metadata agent failed ($($result.ExitCode))." }; $metadata = ConvertFrom-MetadataOutput $result.Output
                }
                'kimi' {
                    $arguments = @('-p', $prompt, '--output-format', 'text'); if ($model) { $arguments += @('--model', $model) }
                    $result = Invoke-LoggedCommand kimi $arguments (Join-Path $RunDir 'metadata-agent.log') -AllowFailure
                    if ($result.ExitCode -ne 0) { throw "kimi metadata agent failed ($($result.ExitCode))." }; $metadata = ConvertFrom-MetadataOutput $result.Output
                }
            }
        } finally { Pop-Location }
        Assert-DeliveryMetadata $metadata $State.current.number
        return $metadata
    } catch {
        Add-Content -LiteralPath (Join-Path $RunDir 'metadata-agent.log') -Value "Fallback: $($_.Exception.Message)"
        if (-not $agent.fallback) { throw }
        return $fallback
    } finally {
        $expected = [IO.Path]::GetFullPath($temporary)
        if ($expected.StartsWith([IO.Path]::GetFullPath([IO.Path]::GetTempPath()), [StringComparison]::OrdinalIgnoreCase) -and (Split-Path $expected -Leaf) -like 'deliver-metadata-*') {
            Remove-Item -LiteralPath $expected -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}

function Wait-PullRequestChecks([int]$PrNumber, [string]$HeadSha, [string[]]$Expected, [string]$RunDir, [int]$TimeoutMinutes = 60) {
    $deadline = [DateTimeOffset]::UtcNow.AddMinutes($TimeoutMinutes)
    $expected = @($Expected)
    do {
        $pr = ConvertFrom-CommandJson (Invoke-LoggedCommand gh @('pr', 'view', [string]$PrNumber, '--json', 'headRefOid,isDraft,mergeStateStatus,reviewDecision,state,url') (Join-Path $RunDir 'ci.log')) 'gh pr view'
        if ($pr.headRefOid -ne $HeadSha) { throw (New-QueueException 'PR head changed after local testing.' $script:ExitCodes.Drift) }
        if ($pr.isDraft -or $pr.mergeStateStatus -eq 'DIRTY' -or $pr.reviewDecision -eq 'CHANGES_REQUESTED') {
            throw (New-QueueException 'PR is draft, conflicted, or has changes requested.' $script:ExitCodes.CI)
        }
        $raw = Invoke-LoggedCommand gh @('pr', 'checks', [string]$PrNumber, '--json', 'name,state,link,bucket') (Join-Path $RunDir 'ci.log') -AllowFailure
        $checks = if ($raw.Output) { @(ConvertFrom-CommandJson $raw 'gh pr checks') } else { @() }
        $byName = @{}; foreach ($check in $checks) { $byName[$check.name] = $check }
        $failed = @($expected | Where-Object { $byName.ContainsKey($_) -and $byName[$_].bucket -in @('fail', 'cancel') })
        if ($failed.Count) { throw (New-QueueException ("CI failed: " + ($failed -join ', ')) $script:ExitCodes.CI) }
        $passed = @($expected | Where-Object { $byName.ContainsKey($_) -and $byName[$_].bucket -eq 'pass' })
        if ($passed.Count -eq $expected.Count) { return [pscustomobject]@{ checks = $checks; pr = $pr } }
        if ([DateTimeOffset]::UtcNow -ge $deadline) { break }
        Start-Sleep -Seconds 15
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw (New-QueueException 'Timed out waiting for all required CI checks to appear and pass.' $script:ExitCodes.CI)
}

function Assert-Audit([object]$Audit, [object[]]$Checkboxes, [object]$State, [string]$Root) {
    if (@($Audit.criteria).Count -ne $Checkboxes.Count) { throw 'Audit did not return exactly one result per checkbox.' }
    $successful = @($State.current.localChecks | Where-Object exitCode -eq 0 | ForEach-Object command)
    $ciUrls = @($State.current.ciChecks | Where-Object bucket -eq 'pass' | ForEach-Object link)
    for ($i = 0; $i -lt $Checkboxes.Count; $i++) {
        $criterion = $Audit.criteria[$i]
        if ($criterion.index -ne $i -or $criterion.text -cne $Checkboxes[$i].text) { throw "Audit checkbox mismatch at index $i." }
        if ($criterion.status -eq 'satisfied') {
            if (@($criterion.evidence).Count -eq 0) { throw "Satisfied checkbox $i has no evidence." }
            foreach ($evidence in @($criterion.evidence)) {
                $valid = switch ($evidence.kind) {
                    'file' {
                        $value = $evidence.value
                        if ($value -match '^(?<path>.+):\d+$') { $value = $Matches['path'] }
                        $path = Join-Path $Root $value
                        Test-Path -LiteralPath $path -PathType Leaf
                    }
                    'command' { $evidence.value -in $successful }
                    'ci' { $evidence.value -in $ciUrls }
                    default { $false }
                }
                if (-not $valid) { throw "Unverifiable evidence for checkbox ${i}: $($evidence.kind) $($evidence.value)" }
            }
        }
    }
}

function Set-SatisfiedCheckboxes([string]$Body, [object]$Audit) {
    $script:checkboxReplaceIndex = -1
    return [regex]::Replace($Body, '(?m)^(?<prefix>\s*[-*]\s+)\[(?<mark>[ xX])\](?<suffix>\s+)(?<text>.+?)\s*$', {
        param($match)
        $script:checkboxReplaceIndex++
        if ($Audit.criteria[$script:checkboxReplaceIndex].status -ne 'satisfied') { return $match.Value }
        $offset = $match.Groups['mark'].Index - $match.Index
        return $match.Value.Substring(0, $offset) + 'x' + $match.Value.Substring($offset + 1)
    })
}

function Test-HumanAcceptance([object]$State, [object]$Issue, [string]$LogPath) {
    $live = @(Get-Checkboxes $Issue.body)
    $original = @($State.current.checkboxes)
    if ($live.Count -ne $original.Count) { return $false }
    for ($i = 0; $i -lt $original.Count; $i++) {
        if ($live[$i].text -cne $original[$i].text -or -not $live[$i].checked) { return $false }
    }
    $login = (ConvertFrom-CommandJson (Invoke-LoggedCommand gh @('api', 'user') $LogPath) 'gh api user').login
    $approval = "/accept $($State.current.testedSha)"
    return @($Issue.comments | Where-Object { $_.author.login -eq $login -and $_.body.Trim() -ceq $approval }).Count -gt 0
}

function Get-Preview([object]$Queue, [object]$Policy) {
    $lines = @('PREVIEW: validate tools, authentication, repository, clean base branch, labels, and squash setting')
    foreach ($item in @($Queue.issues)) {
        $branch = "$($Policy.branchPrefix)$($item.number)"
        $lines += "#$($item.number): fetch and fast-forward $($Queue.baseBranch); create $branch; skills=$(@($item.skills) -join ',')"
        $lines += "#$($item.number): invoke implementation agent; run $(@($Policy.localChecks.name) -join ', ')"
        $lines += "#$($item.number): generate metadata with $($Policy.metadataAgent.provider); commit, push, create PR; wait for $(@($Policy.requiredChecks) -join ', ')"
        $lines += "#$($item.number): audit checkboxes; update issue evidence; enforce human gate when required"
        $lines += "#$($item.number): squash merge at tested SHA; delete only $branch; fast-forward $($Queue.baseBranch)"
    }
    $lines += 'PREVIEW: remove the successful run directory; preserve failed state and .scratch/'
    return $lines
}

function Get-IssueQueueExitCodes { return $script:ExitCodes.Clone() }

Export-ModuleMember -Function Read-IssueQueue, Read-RepositoryPolicy, ConvertFrom-IssueSelector, Resolve-IssueSelection, Get-IssueQueueExitCodes, Get-Preview, Assert-Preflight, Save-Json, Invoke-LoggedCommand, ConvertFrom-CommandJson, Get-Checkboxes, Assert-NewBranchAvailable, Invoke-AgentPhase, Invoke-LocalGates, Get-DeliveryMetadata, Wait-PullRequestChecks, Assert-Audit, Set-SatisfiedCheckboxes, Test-HumanAcceptance
