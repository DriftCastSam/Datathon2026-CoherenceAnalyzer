#!/usr/bin/env bash
#
# deploy.sh — mechanizes README.md's "AWS deploy" section, specifically the
# "Flow B on Step Functions (P2.15)" and "Live API Lambda (Function URL)"
# subsections. Those two subsections document the exact manual shell-command
# sequence this script runs: build a Lambda zip (coherence_analyzer +
# data/starter + data/meta + fixtures with pinned manylinux2014/py3.11
# dependencies), upload it to S3, `aws cloudformation deploy` against
# deploy/aws/coherence-analyzer.cfn.json with the documented parameter
# overrides, then print the stack Outputs.
#
# This script is a shortcut for the commands README already documents in
# full — the README listing stays the source of truth; if either drifts,
# update both.
#
# Usage:
#   deploy/aws/deploy.sh --bucket <artifact-bucket> --model-arn <arn> [options]
#
# Required:
#   --bucket <name>            S3 artifact bucket for the Lambda zip (README's
#                               "<artifact bucket>")
#   --model-arn <arn>          Bedrock model/inference-profile ARN -> ModelArn
#
# Optional (README defaults shown):
#   --prefix <prefix>          S3Prefix, app's own data prefix (default: ca/)
#   --model-id <id>            BedrockModelId (default: template default,
#                               us.anthropic.claude-sonnet-5-5)
#   --foundation-model-id <id> FoundationModelId (default: template default,
#                               anthropic.claude-sonnet-5-5)
#   --stack-name <name>        CloudFormation stack name (default: coherence-analyzer)
#   --region <region>          AWS region (default: $AWS_REGION, then
#                               $AWS_DEFAULT_REGION, then us-east-1)
#   --dry-run                  Run build + upload (steps 1-2) only; print the
#                               `aws cloudformation deploy` command instead of
#                               running it.
#   -h, --help                 Show this usage message.
#
# Env var equivalents (flags take precedence):
#   DEPLOY_BUCKET, DEPLOY_PREFIX, DEPLOY_MODEL_ARN, DEPLOY_MODEL_ID,
#   DEPLOY_FOUNDATION_MODEL_ID, DEPLOY_STACK_NAME, AWS_REGION/AWS_DEFAULT_REGION
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TEMPLATE_FILE="${SCRIPT_DIR}/coherence-analyzer.cfn.json"

usage() {
  cat <<'EOF'
Usage: deploy/aws/deploy.sh --bucket <artifact-bucket> --model-arn <arn> [options]

Required:
  --bucket <name>             S3 artifact bucket for the Lambda zip
  --model-arn <arn>           Bedrock model/inference-profile ARN (ModelArn)

Optional:
  --prefix <prefix>           S3Prefix, default ca/
  --model-id <id>             BedrockModelId
  --foundation-model-id <id>  FoundationModelId
  --stack-name <name>         CloudFormation stack name, default coherence-analyzer
  --region <region>           AWS region, default $AWS_REGION/$AWS_DEFAULT_REGION/us-east-1
  --dry-run                   Build + upload only; print the deploy command, don't run it
  -h, --help                  Show this message

Mirrors README.md's "AWS deploy" section ("Flow B on Step Functions (P2.15)"
and "Live API Lambda (Function URL)" subsections). See that section for the
manual command listing this script automates.
EOF
}

# ---- defaults -------------------------------------------------------------
BUCKET="${DEPLOY_BUCKET:-}"
PREFIX="${DEPLOY_PREFIX:-ca/}"
MODEL_ARN="${DEPLOY_MODEL_ARN:-}"
MODEL_ID="${DEPLOY_MODEL_ID:-}"
FOUNDATION_MODEL_ID="${DEPLOY_FOUNDATION_MODEL_ID:-}"
STACK_NAME="${DEPLOY_STACK_NAME:-coherence-analyzer}"
REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-us-east-1}}"
DRY_RUN=0

# ---- arg parsing ------------------------------------------------------------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --bucket)
      BUCKET="$2"; shift 2 ;;
    --prefix)
      PREFIX="$2"; shift 2 ;;
    --model-arn)
      MODEL_ARN="$2"; shift 2 ;;
    --model-id)
      MODEL_ID="$2"; shift 2 ;;
    --foundation-model-id)
      FOUNDATION_MODEL_ID="$2"; shift 2 ;;
    --stack-name)
      STACK_NAME="$2"; shift 2 ;;
    --region)
      REGION="$2"; shift 2 ;;
    --dry-run)
      DRY_RUN=1; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "error: unknown argument: $1" >&2
      usage >&2
      exit 1 ;;
  esac
done

# ---- validation -------------------------------------------------------------
missing=()
[[ -z "${BUCKET}" ]] && missing+=("--bucket")
[[ -z "${MODEL_ARN}" ]] && missing+=("--model-arn")

if [[ ${#missing[@]} -gt 0 ]]; then
  echo "error: missing required argument(s): ${missing[*]}" >&2
  echo >&2
  usage >&2
  exit 1
fi

if [[ ! -f "${TEMPLATE_FILE}" ]]; then
  echo "error: template not found at ${TEMPLATE_FILE}" >&2
  exit 1
fi

echo "== coherence-analyzer AWS deploy =="
echo "stack:    ${STACK_NAME}"
echo "region:   ${REGION}"
echo "bucket:   ${BUCKET}"
echo "prefix:   ${PREFIX}"
echo "dry-run:  ${DRY_RUN}"
echo

# ---- step 1: build the Lambda zip -------------------------------------------
# Exactly README's build sequence (Flow B / Live API Lambda subsections):
#   rm -rf build && mkdir build
#   pip install -r requirements.txt --target build --platform manylinux2014_x86_64 \
#       --only-binary=:all: --python-version 3.11
#   cp -r coherence_analyzer build/ && mkdir -p build/data \
#       && cp -r data/starter data/meta build/data/ && cp -r fixtures build/
#   (cd build && zip -qr ../flow-b-lambda.zip . -x '*/__pycache__/*')
echo "-- step 1: build --"
cd "${REPO_ROOT}"

BUILD_DIR="${REPO_ROOT}/build"
rm -rf "${BUILD_DIR}"
mkdir "${BUILD_DIR}"

pip install -r requirements.txt \
  --target "${BUILD_DIR}" \
  --platform manylinux2014_x86_64 \
  --only-binary=:all: \
  --python-version 3.11

cp -r coherence_analyzer "${BUILD_DIR}/"
mkdir -p "${BUILD_DIR}/data"
cp -r data/starter data/meta "${BUILD_DIR}/data/"
cp -r fixtures "${BUILD_DIR}/"

# Name the zip with a timestamp + short git sha so repeated runs never
# silently reuse a stale LambdaCodeS3Key (CloudFormation does not notice a
# changed object under the same key — see README).
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
GIT_SHA="$(git -C "${REPO_ROOT}" rev-parse --short HEAD 2>/dev/null || echo nogit)"
VERSION_TAG="${TIMESTAMP}-${GIT_SHA}"
ZIP_NAME="flow-b-lambda-${VERSION_TAG}.zip"
ZIP_PATH="${REPO_ROOT}/${ZIP_NAME}"

rm -f "${ZIP_PATH}"
(cd "${BUILD_DIR}" && zip -qr "${ZIP_PATH}" . -x '*/__pycache__/*')

echo "built: ${ZIP_PATH}"
echo

# ---- step 2: upload -----------------------------------------------------
# README: aws s3 cp flow-b-lambda.zip s3://<artifact bucket>/coherence-analyzer/flow-b-lambda-<version>.zip
S3_KEY="coherence-analyzer/${ZIP_NAME}"
S3_URI="s3://${BUCKET}/${S3_KEY}"

echo "-- step 2: upload --"
aws s3 cp "${ZIP_PATH}" "${S3_URI}" --region "${REGION}"
echo "uploaded: ${S3_URI}"
echo

# ---- step 3: deploy -------------------------------------------------------
# README: aws cloudformation deploy --template-file ... --stack-name ... \
#   --capabilities CAPABILITY_IAM --parameter-overrides ModelArn=... S3Prefix=... \
#   BedrockModelId=... LambdaCodeS3Bucket=... LambdaCodeS3Key=...
# (ModelArn/S3Prefix are also the base template's parameters, per the
# non-Flow-B "AWS deploy" subsection; FoundationModelId is an additional
# template parameter with its own default.)
PARAM_OVERRIDES=(
  "ModelArn=${MODEL_ARN}"
  "S3Prefix=${PREFIX}"
  "LambdaCodeS3Bucket=${BUCKET}"
  "LambdaCodeS3Key=${S3_KEY}"
)
[[ -n "${MODEL_ID}" ]] && PARAM_OVERRIDES+=("BedrockModelId=${MODEL_ID}")
[[ -n "${FOUNDATION_MODEL_ID}" ]] && PARAM_OVERRIDES+=("FoundationModelId=${FOUNDATION_MODEL_ID}")

DEPLOY_CMD=(aws cloudformation deploy
  --template-file "${TEMPLATE_FILE}"
  --stack-name "${STACK_NAME}"
  --region "${REGION}"
  --capabilities CAPABILITY_IAM
  --parameter-overrides "${PARAM_OVERRIDES[@]}"
)

echo "-- step 3: deploy (DESTRUCTIVE: updates the live stack) --"
if [[ "${DRY_RUN}" -eq 1 ]]; then
  echo "dry-run: would run:"
  printf '  %q' "${DEPLOY_CMD[@]}"
  echo
  echo "dry-run: skipping deploy and output report. Re-run without --dry-run to apply."
  exit 0
fi

"${DEPLOY_CMD[@]}"
echo

# ---- step 4: report --------------------------------------------------------
echo "-- step 4: outputs --"
OUTPUTS_JSON="$(aws cloudformation describe-stacks \
  --stack-name "${STACK_NAME}" \
  --region "${REGION}" \
  --query "Stacks[0].Outputs" \
  --output json)"

echo "${OUTPUTS_JSON}" | jq '.'

API_URL="$(echo "${OUTPUTS_JSON}" | jq -r '.[] | select(.OutputKey=="ApiFunctionUrl") | .OutputValue' 2>/dev/null || true)"
SFN_ARN="$(echo "${OUTPUTS_JSON}" | jq -r '.[] | select(.OutputKey=="StateMachineArn") | .OutputValue' 2>/dev/null || true)"

echo
echo "ApiFunctionUrl:  ${API_URL:-<not present — DeployFlowB condition false or template changed>}"
echo "StateMachineArn: ${SFN_ARN:-<not present — DeployFlowB condition false or template changed>}"
