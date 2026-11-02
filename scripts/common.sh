#!/usr/bin/env bash
set -euo pipefail
export AWS_PAGER=""
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
for tool in aws python3 jq; do
  command -v "$tool" >/dev/null || { echo "Missing tool: $tool (run in AWS CloudShell)." >&2; exit 1; }
done
REGION=$(jq -er '.region' config.json)
STACK=$(jq -er '.stack_name' config.json)
EXPECTED=$(jq -er '.expected_account_id' config.json)
[[ "$REGION" == "ap-northeast-1" ]] || { echo "This prototype is configured for Tokyo only." >&2; exit 1; }
[[ "$STACK" =~ ^[a-z][a-z0-9-]{2,35}$ ]] || { echo "Invalid stack_name." >&2; exit 1; }
[[ "$EXPECTED" =~ ^[0-9]{12}$ ]] || { echo "Set expected_account_id to your 12-digit account ID." >&2; exit 1; }
awsr() { command aws --region "$REGION" --no-cli-pager "$@"; }
ACCOUNT=$(awsr sts get-caller-identity --query Account --output text)
[[ "$ACCOUNT" == "$EXPECTED" ]] || { echo "Wrong AWS account: $ACCOUNT (expected $EXPECTED)." >&2; exit 1; }
BUCKET="${STACK}-artifacts-${ACCOUNT}-${REGION}"
mkdir -p .build
echo "Account=$ACCOUNT  Region=$REGION  Stack=$STACK"

check_bucket_owner() {
  awsr s3api head-bucket --bucket "$BUCKET" --expected-bucket-owner "$ACCOUNT"
  local tags
  tags=$(awsr s3api get-bucket-tagging --bucket "$BUCKET" --output json)
  jq -e --arg stack "$STACK" '([.TagSet[] | select(.Key=="Project" and .Value=="aws-mail-prototype")] | length)==1 and ([.TagSet[] | select(.Key=="StackName" and .Value==$stack)] | length)==1' <<< "$tags" >/dev/null || {
    echo "Bucket ownership tags do not match; refusing to use/delete it." >&2; exit 1;
  }
}
