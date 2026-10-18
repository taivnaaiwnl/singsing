# Deploy Kervan Credit Passport to Cloud Run (Windows PowerShell 5.1+).
#
#   $env:GEMINI_API_KEY = "..."          # only needed the first time (creates the secret); falls back to .env
#   .\deploy.ps1 -ProjectId my-project [-Region asia-northeast1]
param(
    [Parameter(Mandatory = $true)][string]$ProjectId,
    [string]$Region = "asia-northeast1",
    [string]$Service = "kervan-credit-passport",
    [string]$Model = "gemini-3.8-flash",
    [string]$SecretName = "gemini-api-key",
    [string]$RuntimeSaName = "kervan-run"
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

$RuntimeSa = "$RuntimeSaName@$ProjectId.iam.gserviceaccount.com"
Write-Host ">> project=$ProjectId region=$Region service=$Service model=$Model"
gcloud config set project $ProjectId | Out-Null

Write-Host ">> enabling APIs"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com iam.googleapis.com
Assert-Ok "enable APIs"

gcloud secrets describe $SecretName 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    if (-not $env:GEMINI_API_KEY) { throw "Set `$env:GEMINI_API_KEY (or put it in .env) the first time so the secret can be created." }
    Write-Host ">> creating secret $SecretName"
    $tmp = New-TemporaryFile
    [IO.File]::WriteAllText($tmp.FullName, $env:GEMINI_API_KEY)
    gcloud secrets create $SecretName --data-file=$tmp.FullName --replication-policy=automatic
    Remove-Item $tmp.FullName -Force
    Assert-Ok "create secret"
} else {
    Write-Host ">> secret $SecretName exists"
}

# Dedicated runtime identity with no project-wide roles: it may only read the Gemini secret.
gcloud iam service-accounts describe $RuntimeSa 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host ">> creating runtime service account $RuntimeSa"
    gcloud iam service-accounts create $RuntimeSaName --display-name="Kervan Cloud Run runtime"
    Assert-Ok "create service account"
}
Write-Host ">> granting secret access to $RuntimeSa"
gcloud secrets add-iam-policy-binding $SecretName --member="serviceAccount:$RuntimeSa" --role="roles/secretmanager.secretAccessor" | Out-Null

$extraEnv = ""
if ($env:KERVAN_STORE -eq "firestore") {
    $extraEnv = ",KERVAN_STORE=firestore"
    gcloud projects add-iam-policy-binding $ProjectId --member="serviceAccount:$RuntimeSa" --role="roles/datastore.user" | Out-Null
}

Write-Host ">> deploying from source (Cloud Build)"
# KERVAN_DATA_DIR is left to the Dockerfile default (/tmp/kervan-data).
gcloud run deploy $Service `
  --source . `
  --region $Region `
  --allow-unauthenticated `
  --service-account $RuntimeSa `
  --memory 1Gi --cpu 1 `
  --timeout 300 `
  --concurrency 20 `
  --min-instances 0 --max-instances 3 `
  --set-secrets "GEMINI_API_KEY=${SecretName}:latest" `
  --set-env-vars "GEMINI_MODEL=$Model,GEMINI_TIMEOUT_S=120,GEMINI_THINKING_LEVEL=low$extraEnv"
Assert-Ok "gcloud run deploy"

$url = gcloud run services describe $Service --region $Region --format='value(status.url)'
Write-Host ">> deployed: $url"
Write-Host ">> health:"
(Invoke-WebRequest -UseBasicParsing "$url/api/health").Content
Write-Host ">> open $url"
