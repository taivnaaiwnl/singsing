# Deploy Kervan Credit Passport to Cloud Run (Windows PowerShell 5.1+).
#
#   $env:GEMINI_API_KEY = "..."          # only needed the first time (creates the secret)
#   .\deploy.ps1 -ProjectId my-project [-Region asia-northeast1]
param(
    [Parameter(Mandatory = $true)][string]$ProjectId,
    [string]$Region = "asia-northeast1",
    [string]$Service = "kervan-credit-passport",
    [string]$Model = "gemini-3.8-flash",
    [string]$SecretName = "gemini-api-key"
)
# gcloud writes progress to stderr; keep going on those messages and check exit codes explicitly.
$ErrorActionPreference = "Continue"
function Assert-Ok($step) { if ($LASTEXITCODE -ne 0) { throw "$step failed (exit $LASTEXITCODE)" } }

# Fall back to the local .env (never committed) for the Gemini key, without printing it.
if (-not $env:GEMINI_API_KEY) {
    $envFile = Join-Path $PSScriptRoot ".env"
    if (Test-Path $envFile) {
        $line = Get-Content $envFile | Where-Object { $_ -match '^\s*GEMINI_API_KEY\s*=' } | Select-Object -First 1
        if ($line) { $env:GEMINI_API_KEY = ($line -split '=', 2)[1].Trim().Trim('"').Trim("'") }
    }
}

Write-Host ">> project=$ProjectId region=$Region service=$Service model=$Model"
gcloud config set project $ProjectId | Out-Null

Write-Host ">> enabling APIs"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com
Assert-Ok "enable APIs"

$secretExists = $true
try { gcloud secrets describe $SecretName 2>$null | Out-Null; if ($LASTEXITCODE -ne 0) { $secretExists = $false } } catch { $secretExists = $false }
if (-not $secretExists) {
    if (-not $env:GEMINI_API_KEY) { throw "Set `$env:GEMINI_API_KEY the first time so the secret can be created." }
    Write-Host ">> creating secret $SecretName"
    $tmp = New-TemporaryFile
    [IO.File]::WriteAllText($tmp.FullName, $env:GEMINI_API_KEY)
    gcloud secrets create $SecretName --data-file=$tmp.FullName --replication-policy=automatic
    Remove-Item $tmp.FullName -Force
} else {
    Write-Host ">> secret $SecretName exists"
}

$projectNumber = gcloud projects describe $ProjectId --format='value(projectNumber)'
$runtimeSa = "$projectNumber-compute@developer.gserviceaccount.com"
Write-Host ">> granting secret access to $runtimeSa"
gcloud secrets add-iam-policy-binding $SecretName --member="serviceAccount:$runtimeSa" --role="roles/secretmanager.secretAccessor" | Out-Null

Write-Host ">> deploying from source (Cloud Build)"
gcloud run deploy $Service `
  --source . `
  --region $Region `
  --allow-unauthenticated `
  --memory 1Gi --cpu 1 `
  --timeout 300 `
  --concurrency 20 `
  --min-instances 0 --max-instances 3 `
  --set-secrets "GEMINI_API_KEY=${SecretName}:latest" `
  --set-env-vars "GEMINI_MODEL=$Model,KERVAN_DATA_DIR=/tmp/kervan-data,GEMINI_TIMEOUT_S=120,GEMINI_THINKING_LEVEL=low"
Assert-Ok "gcloud run deploy"

$url = gcloud run services describe $Service --region $Region --format='value(status.url)'
Write-Host ">> deployed: $url"
Write-Host ">> health:"
(Invoke-WebRequest -UseBasicParsing "$url/healthz").Content
Write-Host ">> open $url"
