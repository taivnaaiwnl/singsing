#!/usr/bin/env bash
# Deploy Kervan Credit Passport to Cloud Run.
#
#   export GEMINI_API_KEY=...            # only needed the first time (creates the secret)
#   ./deploy.sh <gcp-project-id> [region]
#
# Defaults: region asia-northeast1 (Tokyo), service kervan-credit-passport, model gemini-3.8-flash.
set -euo pipefail

PROJECT_ID="${1:?usage: ./deploy.sh <gcp-project-id> [region]}"
REGION="${2:-asia-northeast1}"

# Fall back to the local .env (never committed) for the Gemini key, without printing it.
if [ -z "${GEMINI_API_KEY:-}" ] && [ -f "$(dirname "$0")/.env" ]; then
  GEMINI_API_KEY="$(grep -E '^\s*GEMINI_API_KEY\s*=' "$(dirname "$0")/.env" | head -1 | cut -d= -f2- | tr -d '"'"'"' ' | tr -d '\r')"
  export GEMINI_API_KEY
fi
SERVICE="${SERVICE:-kervan-credit-passport}"
MODEL="${GEMINI_MODEL:-gemini-3.8-flash}"
SECRET_NAME="gemini-api-key"

echo ">> project=$PROJECT_ID region=$REGION service=$SERVICE model=$MODEL"
gcloud config set project "$PROJECT_ID" >/dev/null

echo ">> enabling APIs"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com

if ! gcloud secrets describe "$SECRET_NAME" >/dev/null 2>&1; then
  : "${GEMINI_API_KEY:?GEMINI_API_KEY must be exported the first time so the secret can be created}"
  echo ">> creating secret $SECRET_NAME"
  printf '%s' "$GEMINI_API_KEY" | gcloud secrets create "$SECRET_NAME" --data-file=- --replication-policy=automatic
else
  echo ">> secret $SECRET_NAME exists (use 'gcloud secrets versions add $SECRET_NAME --data-file=-' to rotate)"
fi

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
echo ">> granting secret access to $RUNTIME_SA"
gcloud secrets add-iam-policy-binding "$SECRET_NAME" \
  --member="serviceAccount:${RUNTIME_SA}" --role="roles/secretmanager.secretAccessor" >/dev/null

echo ">> deploying from source (Cloud Build)"
gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --allow-unauthenticated \
  --memory 1Gi --cpu 1 \
  --timeout 300 \
  --concurrency 20 \
  --min-instances 0 --max-instances 3 \
  --set-secrets "GEMINI_API_KEY=${SECRET_NAME}:latest" \
  --set-env-vars "GEMINI_MODEL=${MODEL},KERVAN_DATA_DIR=/tmp/kervan-data,GEMINI_TIMEOUT_S=120,GEMINI_THINKING_LEVEL=low"

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"
echo ">> deployed: $URL"
echo ">> health:"
curl -s "$URL/api/health"; echo
echo ">> config:"
curl -s "$URL/api/config" | python -c 'import json,sys; c=json.load(sys.stdin); print("mode", c["mode"], "| model", c["model"], "| gemini configured", c["gemini_configured"])'
echo ">> open $URL and run an assessment with your documents"
