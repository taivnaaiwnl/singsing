#!/usr/bin/env bash
# Deploy Kervan Credit Passport to Cloud Run.
#
#   export GEMINI_API_KEY=...            # only needed the first time (creates the secret); falls back to .env
#   ./deploy.sh <gcp-project-id> [region]
#
# Defaults: region asia-northeast1 (Tokyo), service kervan-credit-passport, model gemini-3.8-flash.
set -euo pipefail
# Note for Git Bash on Windows: MSYS rewrites /tmp/... style arguments into Windows paths before gcloud
# sees them, so this script passes no slash-prefixed values (KERVAN_DATA_DIR stays at the Dockerfile default).

PROJECT_ID="${1:?usage: ./deploy.sh <gcp-project-id> [region]}"
REGION="${2:-asia-northeast1}"
SERVICE="${SERVICE:-kervan-credit-passport}"
MODEL="${GEMINI_MODEL:-gemini-3.8-flash}"
SECRET_NAME="gemini-api-key"
RUNTIME_SA_NAME="kervan-run"
RUNTIME_SA="${RUNTIME_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

# Fall back to the local .env (never committed) for the Gemini key, without printing it.
if [ -z "${GEMINI_API_KEY:-}" ] && [ -f "$(dirname "$0")/.env" ]; then
  GEMINI_API_KEY="$(grep -E '^\s*GEMINI_API_KEY\s*=' "$(dirname "$0")/.env" | head -1 | cut -d= -f2- | tr -d '"'"'"' ' | tr -d '\r')"
  export GEMINI_API_KEY
fi

echo ">> project=$PROJECT_ID region=$REGION service=$SERVICE model=$MODEL"
gcloud config set project "$PROJECT_ID" >/dev/null

echo ">> enabling APIs"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com iam.googleapis.com

if ! gcloud secrets describe "$SECRET_NAME" >/dev/null 2>&1; then
  : "${GEMINI_API_KEY:?GEMINI_API_KEY must be exported (or present in .env) the first time so the secret can be created}"
  echo ">> creating secret $SECRET_NAME"
  printf '%s' "$GEMINI_API_KEY" | gcloud secrets create "$SECRET_NAME" --data-file=- --replication-policy=automatic
else
  echo ">> secret $SECRET_NAME exists (use 'gcloud secrets versions add $SECRET_NAME --data-file=-' to rotate)"
fi

# Dedicated runtime identity with no project-wide roles: it may only read the Gemini secret.
if ! gcloud iam service-accounts describe "$RUNTIME_SA" >/dev/null 2>&1; then
  echo ">> creating runtime service account $RUNTIME_SA"
  gcloud iam service-accounts create "$RUNTIME_SA_NAME" --display-name="Kervan Cloud Run runtime"
fi
echo ">> granting secret access to $RUNTIME_SA"
gcloud secrets add-iam-policy-binding "$SECRET_NAME" \
  --member="serviceAccount:${RUNTIME_SA}" --role="roles/secretmanager.secretAccessor" >/dev/null

EXTRA_ENV=""
if [ "${KERVAN_STORE:-}" = "firestore" ]; then
  EXTRA_ENV=",KERVAN_STORE=firestore"
  gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:${RUNTIME_SA}" --role="roles/datastore.user" >/dev/null
fi

echo ">> deploying from source (Cloud Build)"
# KERVAN_DATA_DIR is left to the Dockerfile default (/tmp/kervan-data).
gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --allow-unauthenticated \
  --service-account "$RUNTIME_SA" \
  --memory 1Gi --cpu 1 \
  --timeout 300 \
  --concurrency 20 \
  --min-instances 0 --max-instances 3 \
  --set-secrets "GEMINI_API_KEY=${SECRET_NAME}:latest" \
  --set-env-vars "GEMINI_MODEL=${MODEL},GEMINI_TIMEOUT_S=120,GEMINI_THINKING_LEVEL=low${EXTRA_ENV}"

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
echo ">> deployed: $URL"
echo ">> health:"
curl -s "$URL/api/health"; echo
echo ">> config:"
curl -s "$URL/api/config" | python -c 'import json,sys; c=json.load(sys.stdin); print("mode", c["mode"], "| model", c["model"], "| gemini configured", c["gemini_configured"])'
echo ">> open $URL and run an assessment with your documents"
