[CmdletBinding()]
param(
    [string]$OutputDirectory = (Join-Path $env:USERPROFILE '.copilot\docs\ARGUS\conduent-bakeoff-v1\source'),
    [string]$ResourceGroup = 'rg-argus-dev',
    [string]$JobName = 'argus-cu-eval',
    [string]$WorkspaceName = 'law-53eugbsj5xbfy'
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$launchOutput = & "$PSScriptRoot\run_conduent_bakeoff.ps1" `
    -Stage Export `
    -ResourceGroup $ResourceGroup `
    -JobName $JobName `
    -SkipImageBuild
$launch = @($launchOutput | Where-Object { $_ -is [pscustomobject] -and $_.Execution }) | Select-Object -Last 1
if (-not $launch) {
    throw 'The evaluation export job did not return an execution name.'
}
$execution = $launch.Execution

$deadline = (Get-Date).AddMinutes(10)
do {
    $jobExecution = az containerapp job execution show `
        --resource-group $ResourceGroup `
        --name $JobName `
        --job-execution-name $execution `
        --output json `
        --only-show-errors | ConvertFrom-Json -Depth 100
    $status = $jobExecution.properties.status
    if ($status -in @('Succeeded', 'Failed', 'Stopped')) {
        break
    }
    Start-Sleep -Seconds 8
} while ((Get-Date) -lt $deadline)
if ($status -ne 'Succeeded') {
    throw "Evaluation export job '$execution' finished with status '$status'."
}

$workspaceId = az monitor log-analytics workspace show `
    --resource-group $ResourceGroup `
    --workspace-name $WorkspaceName `
    --query customerId `
    --output tsv `
    --only-show-errors
if (-not $workspaceId) {
    throw "Unable to resolve Log Analytics workspace '$WorkspaceName'."
}

$rows = @()
for ($attempt = 1; $attempt -le 30; $attempt++) {
    $query = @"
ContainerAppConsoleLogs_CL
| where ContainerGroupName_s startswith '$execution'
| where Log_s startswith 'ARGUS_EXPORT_'
| project Log_s
"@
    $queryResult = az monitor log-analytics query `
        --workspace $workspaceId `
        --analytics-query $query `
        --output json `
        --only-show-errors
    if ($queryResult) {
        $rows = @($queryResult | ConvertFrom-Json -Depth 100)
    }
    $start = $rows | Where-Object { $_.Log_s -like 'ARGUS_EXPORT_START *' } | Select-Object -First 1
    $end = $rows | Where-Object { $_.Log_s -like 'ARGUS_EXPORT_END *' } | Select-Object -First 1
    $chunkRows = @($rows | Where-Object { $_.Log_s -like 'ARGUS_EXPORT_CHUNK *' })
    if ($start -and $end) {
        $metadata = $start.Log_s.Substring('ARGUS_EXPORT_START '.Length) | ConvertFrom-Json -Depth 100
        if ($chunkRows.Count -eq [int]$metadata.chunks) {
            break
        }
    }
    Start-Sleep -Seconds 10
}
if (-not $start -or -not $end -or $chunkRows.Count -ne [int]$metadata.chunks) {
    throw "The export logs for '$execution' were not fully ingested."
}

$chunks = foreach ($row in $chunkRows) {
    if ($row.Log_s -notmatch '^ARGUS_EXPORT_CHUNK (?<index>\d+)/(?<total>\d+) (?<data>.+)$') {
        throw "Malformed export chunk: $($row.Log_s)"
    }
    [pscustomobject]@{
        Index = [int]$Matches.index
        Data = $Matches.data
    }
}
$encoded = (($chunks | Sort-Object Index).Data -join '')
$archive = [Convert]::FromBase64String($encoded)
$digest = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($archive)).ToLowerInvariant()
if ($digest -ne $metadata.sha256) {
    throw "Export archive hash mismatch. Expected $($metadata.sha256), received $digest."
}

$destination = [IO.Path]::GetFullPath($OutputDirectory)
[IO.Directory]::CreateDirectory($destination) | Out-Null
$archivePath = Join-Path $destination 'conduent-bakeoff-source.zip'
[IO.File]::WriteAllBytes($archivePath, $archive)
Expand-Archive -Path $archivePath -DestinationPath $destination -Force

[pscustomobject]@{
    Execution = $execution
    OutputDirectory = $destination
    Archive = $archivePath
    Bytes = $archive.Length
    Sha256 = $digest
    Artifacts = $metadata.artifacts
}
