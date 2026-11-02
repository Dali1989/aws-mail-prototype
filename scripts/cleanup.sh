#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
echo "Will delete stack $STACK, its function/API/logs, and dedicated deployment artifacts."
echo "Will ALSO empty and delete the dedicated task bucket, including job snapshots/results and any older uploaded lists."
echo "Will KEEP SES verified identities, other stacks, practice01, and budgets."
read -r -p "Type $STACK to confirm: " answer
[[ "$answer" == "$STACK" ]] || { echo "Cancelled."; exit 1; }
if awsr cloudformation describe-stacks --stack-name "$STACK" >.build/cleanup-stack.json 2>.build/cleanup-error.txt; then
  csv_bucket=$(jq -r '.Stacks[0].Outputs[]? | select(.OutputKey=="CsvBucketName") | .OutputValue' .build/cleanup-stack.json)
  if [[ -z "$csv_bucket" ]]; then
    csv_bucket=$(awsr cloudformation list-stack-resources --stack-name "$STACK" --query 'StackResourceSummaries[?LogicalResourceId==`CsvBucket`].PhysicalResourceId | [0]' --output text)
  fi
  if [[ -n "$csv_bucket" && "$csv_bucket" != "None" ]]; then
    awsr s3api head-bucket --bucket "$csv_bucket" --expected-bucket-owner "$ACCOUNT"
    csv_tags=$(awsr s3api get-bucket-tagging --bucket "$csv_bucket" --output json)
    jq -e --arg stack "$STACK" '([.TagSet[] | select(.Key=="Project" and .Value=="aws-mail-prototype")] | length)==1 and ([.TagSet[] | select(.Key=="StackName" and .Value==$stack)] | length)==1' <<< "$csv_tags" >/dev/null
    # This bucket is dedicated to this stack; the earlier confirmation includes its data.
    awsr s3 rm "s3://$csv_bucket/" --recursive
  fi
  awsr cloudformation delete-stack --stack-name "$STACK"
  awsr cloudformation wait stack-delete-complete --stack-name "$STACK"
elif ! grep -q 'does not exist' .build/cleanup-error.txt; then
  cat .build/cleanup-error.txt >&2
  exit 1
fi
if awsr s3api head-bucket --bucket "$BUCKET" --expected-bucket-owner "$ACCOUNT" 2>.build/bucket-error.txt; then
  check_bucket_owner
  awsr s3 rm "s3://$BUCKET/$STACK/" --recursive
  # rb fails instead of deleting unexpected objects elsewhere in the bucket.
  awsr s3 rb "s3://$BUCKET"
elif ! grep -Eq '404|Not Found|NoSuchBucket' .build/bucket-error.txt; then
  cat .build/bucket-error.txt >&2
  exit 1
fi
echo "Cleanup completed for this package. Check the console for any unrelated earlier resources."
