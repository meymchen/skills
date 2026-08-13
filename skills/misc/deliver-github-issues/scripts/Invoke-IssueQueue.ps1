[CmdletBinding(DefaultParameterSetName = 'New', SupportsShouldProcess)]
param(
    [Parameter(Mandatory, ParameterSetName = 'New')]
    [string]$Queue,

    [Parameter(Mandatory, ParameterSetName = 'Issues')]
    [Alias('Issue')]
    [string]$Issues,

    [Parameter(Mandatory, ParameterSetName = 'Resume')]
    [ValidatePattern('^[0-9]{8}T[0-9]{6}Z-[a-f0-9]{8}$')]
    [string]$Resume,

    [Parameter(ParameterSetName = 'Resume')]
    [string]$Instruction = '',

    [Parameter(ParameterSetName = 'New')]
    [Parameter(ParameterSetName = 'Issues')]
    [string]$Config = '.github/deliver-github-issues.json'
)

$arguments = @{}
if ($PSCmdlet.ParameterSetName -eq 'New') { $arguments.Queue = $Queue }
elseif ($PSCmdlet.ParameterSetName -eq 'Issues') { $arguments.Issues = $Issues }
else { $arguments.Resume = $Resume; $arguments.Instruction = $Instruction }
if ($PSCmdlet.ParameterSetName -ne 'Resume') { $arguments.Config = $Config }
if ($WhatIfPreference) { $arguments.WhatIf = $true }
& (Join-Path $PSScriptRoot 'Invoke-IssueQueueCore.ps1') @arguments
exit $LASTEXITCODE
