[CmdletBinding()]
param(
    [ValidateSet('Pilot', 'Tune', 'Freeze', 'Holdout', 'Score', 'Finalize', 'Report')]
    [string]$Stage = 'Pilot',
    [string]$ResourceGroup = 'rg-argus-dev',
    [string]$BackendApp = '',
    [string]$JobName = 'argus-cu-eval',
    [string]$RunName = 'conduent-bakeoff-v1',
    [string]$Datasets = 'cms1500,commercial-documents,enrollments,invoice-demo',
    [string]$Variants = 'baseline-current,semantic-guided,selective-confidence,full-confidence,selective-confidence-preprocessed',
    [int]$PilotLimit = 1,
    [switch]$SkipImageBuild
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

if (-not $BackendApp) {
    $BackendApp = (
        az containerapp list `
            --resource-group $ResourceGroup `
            --output json `
            --only-show-errors | ConvertFrom-Json -Depth 100 |
            Where-Object { $_.tags.'azd-service-name' -eq 'backend' } |
            Select-Object -First 1
    ).name
}
if (-not $BackendApp) {
    throw "No backend Container App was found in resource group '$ResourceGroup'."
}

$backend = az containerapp show `
    --resource-group $ResourceGroup `
    --name $BackendApp `
    --output json `
    --only-show-errors | ConvertFrom-Json -Depth 100

$container = $backend.properties.template.containers[0]
$identityId = @($backend.identity.userAssignedIdentities.PSObject.Properties.Name)[0]
if (-not $identityId) {
    throw "Backend Container App '$BackendApp' does not have a user-assigned managed identity."
}
$registry = $backend.properties.configuration.registries[0]
$registryName = ($registry.server -split '\.')[0]
$storageAccountName = ($container.env | Where-Object name -eq 'AZURE_STORAGE_ACCOUNT_NAME').value
$blobAccountUrl = ($container.env | Where-Object name -eq 'BLOB_ACCOUNT_URL').value
$datasetContainer = ($container.env | Where-Object name -eq 'CONTAINER_NAME').value
if (-not $storageAccountName -or -not $blobAccountUrl -or -not $datasetContainer) {
    throw "Backend Container App is missing Azure Storage environment settings."
}

$evaluationContainer = $datasetContainer
$corpusPrefix = 'evaluation/corpus/conduent-v1'
$outputPrefix = "evaluation/results/$RunName"
$corpusTag = (Get-FileHash 'demo\conduent-datasets\catalog.json' -Algorithm SHA256).Hash.Substring(0, 12).ToLowerInvariant()
$codeTag = (git rev-parse --short=12 HEAD).Trim().ToLowerInvariant()
if (-not $codeTag) {
    throw 'Unable to resolve the current Git commit for the evaluation image tag.'
}
$imageTag = "$corpusTag-$codeTag"
$evaluationImage = "$($registry.server)/argus/evaluation:$imageTag"

if (-not $SkipImageBuild) {
    az acr build `
        --registry $registryName `
        --image "argus/evaluation:$imageTag" `
        --file 'src/containerapp/Dockerfile.evaluation' `
        --build-arg "BASE_IMAGE=$($container.image)" `
        --only-show-errors `
        .
}

$requiredEnvironmentNames = @(
    'AZURE_CONTENT_UNDERSTANDING_ENDPOINT',
    'CONTENT_UNDERSTANDING_API_VERSION',
    'CONTENT_UNDERSTANDING_COMPLETION_MODEL',
    'CONTENT_UNDERSTANDING_EMBEDDING_MODEL',
    'DOCUMENT_INTELLIGENCE_ENDPOINT',
    'AZURE_OPENAI_ENDPOINT',
    'AZURE_OPENAI_MODEL_DEPLOYMENT_NAME',
    'AZURE_LOCATION',
    'AZURE_CLIENT_ID',
    'PRICING_USE_RETAIL_API'
)
$environment = @()
foreach ($name in $requiredEnvironmentNames) {
    $entry = $container.env | Where-Object name -eq $name | Select-Object -First 1
    if ($entry -and $entry.value) {
        $environment += "$name=$($entry.value)"
    }
}
$environment += @(
    "BLOB_ACCOUNT_URL=$blobAccountUrl",
    "AZURE_STORAGE_ACCOUNT_NAME=$storageAccountName",
    "EVALUATION_BLOB_CONTAINER=$evaluationContainer",
    "EVALUATION_CORPUS_BLOB_PREFIX=$corpusPrefix",
    "EVALUATION_OUTPUT_BLOB_PREFIX=$outputPrefix",
    'EVALUATION_PUBLISH_CORPUS=true',
    "EVALUATION_STAGE=$Stage",
    "EVALUATION_DATASETS=$Datasets",
    "EVALUATION_VARIANTS=$Variants",
    "EVALUATION_PILOT_LIMIT=$PilotLimit",
    'EVALUATION_OUTPUT_DIR=/tmp/argus-evaluation',
    'CONDUENT_CORPUS_CACHE=/tmp/conduent-datasets'
)

$jobCount = az containerapp job list `
    --resource-group $ResourceGroup `
    --query "[?name=='$JobName'] | length(@)" `
    --output tsv `
    --only-show-errors

if ([int]$jobCount -gt 0) {
    $updateArguments = @(
        'containerapp', 'job', 'update',
        '--resource-group', $ResourceGroup,
        '--name', $JobName,
        '--image', $evaluationImage,
        '--container-name', $JobName,
        '--cpu', '2.0',
        '--memory', '4.0Gi',
        '--replica-timeout', '7200',
        '--replica-retry-limit', '0',
        '--command', 'python',
        '--args', '/app/evaluation/job_entrypoint.py'
    ) + @(
        '--replace-env-vars'
    ) + $environment + @(
        '--only-show-errors',
        '--output', 'none'
    )
    & az @updateArguments
} else {
    $createArguments = @(
        'containerapp', 'job', 'create',
        '--resource-group', $ResourceGroup,
        '--name', $JobName,
        '--environment', $backend.properties.environmentId,
        '--trigger-type', 'Manual',
        '--image', $evaluationImage,
        '--container-name', $JobName,
        '--cpu', '2.0',
        '--memory', '4.0Gi',
        '--replica-timeout', '7200',
        '--replica-retry-limit', '0',
        '--replica-completion-count', '1',
        '--parallelism', '1',
        '--mi-user-assigned', $identityId,
        '--registry-server', $registry.server,
        '--registry-identity', $identityId,
        '--command', 'python',
        '--env-vars'
    ) + $environment + @(
        '--args', '/app/evaluation/job_entrypoint.py'
    ) + @(
        '--only-show-errors',
        '--output', 'none'
    )
    & az @createArguments
}

$execution = az containerapp job start `
    --resource-group $ResourceGroup `
    --name $JobName `
    --query name `
    --output tsv `
    --only-show-errors

[pscustomobject]@{
    Job = $JobName
    Execution = $execution
    Stage = $Stage
    Corpus = "$evaluationContainer/$corpusPrefix"
    Results = "$evaluationContainer/$outputPrefix"
}
