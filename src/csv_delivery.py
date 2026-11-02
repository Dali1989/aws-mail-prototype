"""Small S3 CSV jobs; no automatic retry of an attempted recipient."""
import json
import os
import re
import time
import uuid
import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


def storage():
    return boto3.client('s3', config=Config(connect_timeout=3, read_timeout=5, retries={'total_max_attempts': 1}))


def missing(error):
    return error.response.get('Error', {}).get('Code') in ('NoSuchKey', '404', 'NotFound')


def load(s3, bucket, key):
    obj = s3.get_object(Bucket=bucket, Key=key)
    try:
        return json.loads(obj['Body'].read(500000).decode('utf-8'))
    finally:
        obj['Body'].close()


def save(s3, bucket, key, value, **extra):
    return s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(value, ensure_ascii=False).encode('utf-8'),
                         ContentType='application/json; charset=utf-8', **extra)


def validate_rows(data, allowed, limit):
    if not isinstance(data, list) or not 1 <= len(data) <= limit:
        raise ValueError(f'宛先は1～{limit}件にしてください。')
    rows, seen = [], set()
    for index, item in enumerate(data):
        if not isinstance(item, dict) or set(item) != {'companyName', 'contactName', 'email'}:
            raise ValueError(f'{index+1}件目の会社名・担当者名・メールを確認してください。')
        if any(not isinstance(v, str) for v in item.values()):
            raise ValueError('宛先の各項目は文字列で指定してください。')
        row = {k: v.strip() for k, v in item.items()}
        for field in ('companyName', 'contactName'):
            value = row[field]
            if not value or len(value) > 100 or '\r' in value or '\n' in value:
                raise ValueError(f'{index+1}件目の会社名・担当者名を確認してください。')
        email = row['email']
        if not re.fullmatch(r'[^\s@,]+@[^\s@,]+\.[^\s@,]+', email) or email not in allowed:
            raise ValueError(f'{index+1}件目のメールをテスト宛先の許可リストと照合してください。')
        if email.casefold() in seen:
            raise ValueError('同じメールアドレスが重複しています。')
        seen.add(email.casefold())
        rows.append(row)
    return rows


def row_status(s3, bucket, prefix, index):
    try:
        return load(s3, bucket, f'{prefix}/result-{index}.json')
    except ClientError as error:
        if not missing(error): raise
    try:
        load(s3, bucket, f'{prefix}/claim-{index}.json')
        return {'state': 'unknown', 'message': '処理中、または結果不明です。自動再送しません。'}
    except ClientError as error:
        if not missing(error): raise
        return {'state': 'pending', 'message': '未処理'}


def handle_csv(path, data, sender, allowed, prepare, send):
    bucket = os.environ.get('CSV_BUCKET', '')
    if not bucket:
        return 400, {'message': 'CSV用S3設定がありません。再デプロイしてください。'}
    s3 = storage()
    try:
        if path == '/csv/preview':
            rows = validate_rows(data.get('rows'), allowed, min(5, max(1, int(os.environ.get('MAX_BATCH_SIZE', '5')))))
            messages = []
            for index, row in enumerate(rows):
                item = dict(row, to=row['email'], subject=data.get('subject'), body=data.get('body'), sender=data.get('sender', sender))
                to, subject, body = prepare(item, sender, allowed)
                messages.append(dict(index=index, companyName=row['companyName'], contactName=row['contactName'],
                                     to=to, subject=subject, body=body, state='pending'))
            job_id = uuid.uuid4().hex
            job = {'jobId': job_id, 'sender': sender, 'createdAt': int(time.time()), 'rows': messages,
                   'source': {'type': 'browser-csv'}}
            save(s3, bucket, f'jobs/{job_id}/preview.json', job, IfNoneMatch='*')
            return 200, job
        job_id = data.get('jobId', '')
        if not isinstance(job_id, str) or not re.fullmatch(r'[a-f0-9]{32}', job_id):
            raise ValueError('プレビューのジョブIDを確認してください。')
        prefix = f'jobs/{job_id}'
        job = load(s3, bucket, f'{prefix}/preview.json')
        if time.time() - job['createdAt'] > 86400 or job['sender'] != sender:
            raise ValueError('プレビューが期限切れ、または送信元が変更されています。')
        if path == '/csv/status':
            for row in job['rows']: row.update(row_status(s3, bucket, prefix, row['index']))
            return 200, job
        index = data.get('index')
        if type(index) is not int or not 0 <= index < len(job['rows']):
            raise ValueError('送信対象の行番号を確認してください。')
        row = job['rows'][index]
        if row['to'] not in allowed:
            raise ValueError('この宛先は現在の許可リストにありません。')
        try:
            save(s3, bucket, f'{prefix}/claim-{index}.json', {'startedAt': int(time.time())}, IfNoneMatch='*')
        except ClientError as error:
            if error.response.get('Error', {}).get('Code') not in ('PreconditionFailed', '412', 'ConditionalRequestConflict', '409'): raise
            result = row_status(s3, bucket, prefix, index)
            return (200 if result['state'] == 'accepted' else 409), {'index': index, **result}
        status, result = send(sender, row['to'], row['subject'], row['body'])
        outcome = {'index': index, 'state': 'accepted' if status == 200 else 'unknown', **result}
        try:
            save(s3, bucket, f'{prefix}/result-{index}.json', outcome)
        except (ClientError, BotoCoreError):
            return 502, {'index': index, 'state': 'unknown', 'message': '結果の保存に失敗しました。受信箱を確認し、自動再送しないでください。'}
        return status, outcome
    except ValueError as error:
        return 400, {'message': str(error)}
    except ClientError as error:
        if missing(error):
            return 404, {'message': 'ジョブがありません。ジョブIDと有効期限を確認してください。'}
        print(json.dumps({'event': 'csv_storage_error', 'code': error.response.get('Error', {}).get('Code')}))
        return 502, {'message': 'S3処理に失敗しました。権限とログを確認してください。自動再送しません。'}
    except BotoCoreError:
        return 502, {'message': 'S3通信結果が不明です。結果確認を行い、自動再送しないでください。'}
