#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/common.sh"
python3 - <<'PY'
import json,re
c=json.load(open('config.json'))
items=[c['sender_email'],*c['allowed_recipients']]
assert type(c.get('max_batch_size')) is int and 1 <= c['max_batch_size'] <= 5, 'max_batch_size must be 1 to 5.'
assert c['allowed_recipients'] and len(c['allowed_recipients']) <= 5, 'Use 1 to 5 test recipients.'
assert all(isinstance(x,str) and re.fullmatch(r'[^\s@,]+@[^\s@,]+\.[^\s@,]+',x) for x in items), 'Invalid email setting.'
PY
SENDER=$(jq -er '.sender_email' config.json)
VERIFIED=$(awsr sesv2 get-email-identity --email-identity "$SENDER" --query VerifiedForSendingStatus --output text)
[[ "$VERIFIED" == "True" || "$VERIFIED" == "true" ]] || { echo "Verify sender in Tokyo SES first: $SENDER" >&2; exit 1; }
PRODUCTION=$(awsr sesv2 get-account --query ProductionAccessEnabled --output text)
if [[ "$PRODUCTION" != "True" && "$PRODUCTION" != "true" ]]; then
  while IFS= read -r email; do
    verified=$(awsr sesv2 get-email-identity --email-identity "$email" --query VerifiedForSendingStatus --output text)
    [[ "$verified" == "True" || "$verified" == "true" ]] || { echo "Verify sandbox recipient first: $email" >&2; exit 1; }
  done < <(jq -r '.allowed_recipients[]' config.json)
fi
if ! awsr s3api head-bucket --bucket "$BUCKET" --expected-bucket-owner "$ACCOUNT" 2>/dev/null; then
  awsr s3api create-bucket --bucket "$BUCKET" --create-bucket-configuration "LocationConstraint=$REGION" >/dev/null
  awsr s3api put-bucket-tagging --bucket "$BUCKET" --tagging "TagSet=[{Key=Project,Value=aws-mail-prototype},{Key=StackName,Value=$STACK}]"
fi
check_bucket_owner
awsr s3api put-public-access-block --bucket "$BUCKET" --public-access-block-configuration BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
awsr s3api put-bucket-encryption --bucket "$BUCKET" --server-side-encryption-configuration '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"AES256"}}]}'
PARAMS=$(mktemp)
chmod 600 "$PARAMS"
trap 'rm -f -- "$PARAMS"' EXIT
python3 - "$PARAMS" <<'PY'
import json,secrets,sys
c=json.load(open('config.json'))
p={'SenderEmail':c['sender_email'],'AllowedRecipients':','.join(c['allowed_recipients']),'AccessToken':secrets.token_urlsafe(32),'MaxBatchSize':str(c['max_batch_size'])}
with open(sys.argv[1],'w') as f:json.dump([{'ParameterKey':k,'ParameterValue':v} for k,v in p.items()],f)
PY
awsr cloudformation package --template-file template.json --s3-bucket "$BUCKET" --s3-prefix "$STACK" --output-template-file .build/packaged.yaml
awsr cloudformation validate-template --template-body file://.build/packaged.yaml >/dev/null
awsr cloudformation deploy --template-file .build/packaged.yaml --stack-name "$STACK" --capabilities CAPABILITY_IAM CAPABILITY_AUTO_EXPAND --parameter-overrides "file://$PARAMS" --tags Project=aws-mail-prototype --no-fail-on-empty-changeset
URL=$(awsr cloudformation describe-stacks --stack-name "$STACK" --query 'Stacks[0].Outputs[?OutputKey==`PageUrl`].OutputValue | [0]' --output text)
echo ""
echo "Open page: $URL"
echo "Demo access key (copy now; not an AWS access key):"
python3 - "$PARAMS" <<'PY'
import json,sys
print(next(x['ParameterValue'] for x in json.load(open(sys.argv[1])) if x['ParameterKey']=='AccessToken'))
PY
echo "No email has been sent by this script. Each deployment rotates the demo key."

echo "Job storage bucket: $(awsr cloudformation describe-stacks --stack-name "$STACK" --query 'Stacks[0].Outputs[?OutputKey==`CsvBucketName`].OutputValue | [0]' --output text)"
echo "Choose a local CSV in the web page. No S3 upload is needed; no email was sent by deployment."
