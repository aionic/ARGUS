[CmdletBinding()]
param(
    [string]$OutputDirectory = (Join-Path $env:USERPROFILE '.copilot\docs\ARGUS\conduent-bakeoff-v1\source'),
    [string]$ResourceGroup = 'rg-argus-dev',
    [string]$JobName = 'argus-cu-eval',
    [string]$WorkspaceName = 'law-53eugbsj5xbfy',
    [string]$Execution = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

function Invoke-AzCapture {
    param([Parameter(Mandatory)][string[]]$Arguments)

    $azCommand = (Get-Command az -ErrorAction Stop).Source
    $azPython = Join-Path (Split-Path -Parent (Split-Path -Parent $azCommand)) 'python.exe'
    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $azPython
    foreach ($argument in @('-IBm', 'azure.cli') + $Arguments) {
        $startInfo.ArgumentList.Add($argument)
    }
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.StandardOutputEncoding = [Text.Encoding]::UTF8
    $startInfo.StandardErrorEncoding = [Text.Encoding]::UTF8
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    [void]$process.Start()
    $standardOutput = $process.StandardOutput.ReadToEnd()
    $standardError = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    if ($process.ExitCode -ne 0) {
        throw "Azure CLI failed with exit code $($process.ExitCode): $standardError"
    }
    return $standardOutput
}

if (-not $Execution) {
    $launchOutput = & "$PSScriptRoot\run_conduent_bakeoff.ps1" `
        -Stage Export `
        -ResourceGroup $ResourceGroup `
        -JobName $JobName `
        -SkipImageBuild
    $launch = @($launchOutput | Where-Object { $_ -is [pscustomobject] -and $_.Execution }) | Select-Object -Last 1
    if (-not $launch) {
        throw 'The evaluation export job did not return an execution name.'
    }
    $Execution = $launch.Execution

    $deadline = (Get-Date).AddMinutes(10)
    do {
        $jobExecution = az containerapp job execution show `
            --resource-group $ResourceGroup `
            --name $JobName `
            --job-execution-name $Execution `
            --output json `
            --only-show-errors | ConvertFrom-Json -Depth 100
        $status = $jobExecution.properties.status
        if ($status -in @('Succeeded', 'Failed', 'Stopped')) {
            break
        }
        Start-Sleep -Seconds 8
    } while ((Get-Date) -lt $deadline)
    if ($status -ne 'Succeeded') {
        throw "Evaluation export job '$Execution' finished with status '$status'."
    }
}

$workspaceId = az monitor log-analytics workspace show `
    --resource-group $ResourceGroup `
    --workspace-name $WorkspaceName `
    --query customerId `
    --output tsv `
    --only-show-errors
$workspaceId = ($workspaceId | Select-Object -Last 1).Trim()
if (-not $workspaceId) {
    throw "Unable to resolve Log Analytics workspace '$WorkspaceName'."
}

$logLines = @()
for ($attempt = 1; $attempt -le 60; $attempt++) {
    $query = @"
ContainerAppConsoleLogs_CL
| where ContainerGroupName_s startswith '$Execution'
| where Log_s startswith 'ARGUS_EXPORT_'
| project Log_s
"@
    $logOutput = Invoke-AzCapture @(
        'monitor',
        'log-analytics',
        'query',
        '--workspace',
        $workspaceId,
        '--analytics-query',
        $query,
        '--query',
        '[].Log_s',
        '--output',
        'tsv',
        '--only-show-errors'
    )
    $logLines = @($logOutput -split '\r?\n' | Where-Object { $_ })
    $start = $logLines | Where-Object { $_ -like 'ARGUS_EXPORT_START *' } | Select-Object -First 1
    $end = $logLines | Where-Object { $_ -like 'ARGUS_EXPORT_END *' } | Select-Object -First 1
    Write-Verbose "Attempt $attempt received $($logLines.Count) export log lines."
    $chunkMap = @{}
    foreach ($line in $logLines | Where-Object { $_ -like 'ARGUS_EXPORT_CHUNK *' }) {
        if ($line -notmatch '^ARGUS_EXPORT_CHUNK (?<index>\d+)/(?<total>\d+) (?<data>.+)$') {
            continue
        }
        $index = [int]$Matches.index
        $data = $Matches.data
        if ($chunkMap.ContainsKey($index) -and $chunkMap[$index] -ne $data) {
            throw "Conflicting export data was found for chunk $index."
        }
        $chunkMap[$index] = $data
    }
    if ($start -and $end) {
        $metadata = $start.Substring('ARGUS_EXPORT_START '.Length) | ConvertFrom-Json -Depth 100
        Write-Verbose "Attempt $attempt found $($chunkMap.Count) of $($metadata.chunks) chunks."
        if ($chunkMap.Count -eq [int]$metadata.chunks) {
            break
        }
    }
    Start-Sleep -Seconds 10
}
if (-not $start -or -not $end -or $chunkMap.Count -ne [int]$metadata.chunks) {
    throw "The export logs for '$Execution' were not fully ingested."
}

$chunks = foreach ($entry in $chunkMap.GetEnumerator()) {
    [pscustomobject]@{
        Index = [int]$entry.Key
        Data = $entry.Value
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
    Execution = $Execution
    OutputDirectory = $destination
    Archive = $archivePath
    Bytes = $archive.Length
    Sha256 = $digest
    Artifacts = $metadata.artifacts
}
