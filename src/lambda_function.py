"""Small SES prototype. Run on Lambda, not a bulk-mail production service."""
import base64
import hmac
import json
import os
import re
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError
from csv_delivery import handle_csv


def reply(status, data, content_type="application/json; charset=utf-8"):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": content_type,
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'",
        },
        "body": json.dumps(data, ensure_ascii=False) if isinstance(data, dict) else data,
    }


def lambda_handler(event, context):
    method = event.get("requestContext", {}).get("http", {}).get("method", "")
    path = event.get("rawPath", "")
    if method == "GET" and path in ("/", "/index.html"):
        return reply(200, Path(__file__).with_name("index.html").read_text(encoding="utf-8"), "text/html; charset=utf-8")

    if method == "GET" and path == "/csv-parser.js":
        return reply(200, Path(__file__).with_name("csv-parser.js").read_text(encoding="utf-8"), "text/javascript; charset=utf-8")
    expected = os.environ.get("ACCESS_TOKEN", "")
    headers = {k.lower(): v for k, v in event.get("headers", {}).items()}
    supplied = headers.get("authorization", "")
    if not expected or not hmac.compare_digest(supplied.encode(), ("Bearer " + expected).encode()):
        return reply(401, {"message": "アクセスキーを確認してください。"})

    sender = os.environ.get("SENDER_EMAIL", "")
    recipients = [s.strip() for s in os.environ.get("ALLOWED_RECIPIENTS", "").split(",") if s.strip()]
    if method == "GET" and path == "/api/config":
        template = Path(__file__).with_name("default-body.txt").read_text(encoding="utf-8").rstrip("\n")
        return reply(200, {"sender": sender, "recipients": recipients, "bodyTemplate": template, "maxBatchSize": int(os.environ.get("MAX_BATCH_SIZE", "5"))})
    if method != "POST" or path not in ("/send", "/csv/preview", "/csv/send", "/csv/status"):
        return reply(404, {"message": "ページが見つかりません。"})

    try:
        raw = event.get("body") or ""
        if event.get("isBase64Encoded"):
            raw = base64.b64decode(raw, validate=True).decode("utf-8")
        if len(raw.encode("utf-8")) > 40000:
            return reply(413, {"message": "入力内容が長すぎます。"})
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("Expected object")
    except (ValueError, UnicodeError, TypeError):
        return reply(400, {"message": "JSON形式の入力を確認してください。"})

    if path.startswith("/csv/"):
        def send_csv(sender, to, subject, body):
            result = send_prepared(sender, to, subject, body, context)
            return result["statusCode"], json.loads(result["body"])
        return reply(*handle_csv(path, data, sender, recipients, prepare_message, send_csv))
    try:
        to, subject, body = prepare_message(data, sender, recipients)
    except ValueError as error:
        return reply(400, {"message": str(error)})
    return send_prepared(sender, to, subject, body, context)


def prepare_message(data, sender, recipients):
    subject, body, to = data.get("subject"), data.get("body"), data.get("to")
    if not isinstance(to, str) or to not in recipients:
        raise ValueError("登録済みのテスト宛先を選択してください。")
    if data.get("sender", sender) != sender:
        raise ValueError("送信元は変更できません。")
    if not isinstance(subject, str) or not subject.strip() or len(subject) > 200 or "\r" in subject or "\n" in subject:
        raise ValueError("件名は改行なし、1～200文字で入力してください。")
    if not isinstance(body, str) or not body.strip() or len(body) > 10000:
        raise ValueError("本文は1～10000文字で入力してください。")

    # Replace once only: customer text must never become a second template.
    tokens = re.compile(r"\{\{\s*([A-Za-z][A-Za-z0-9]*)\s*\}\}")
    values = {}
    for name in set(tokens.findall(body)):
        if name not in ("companyName", "contactName"):
            raise ValueError("本文の変数はcompanyNameとcontactNameのみ使用できます。")
        value = data.get(name)
        if not isinstance(value, str) or not value.strip() or len(value) > 100 or "\n" in value or "\r" in value:
            raise ValueError("送信先会社名と担当者名は改行なし、1～100文字で入力してください。")
        values[name] = value.strip()
    body = tokens.sub(lambda match: values[match.group(1)], body)
    if len(body) > 10000:
        raise ValueError("差し込み後の本文が長すぎます。")

    return to, subject.strip(), body


def send_prepared(sender, to, subject, body, context=None):
    try:
        # Do not automatically retry a mail send when its acceptance is uncertain.
        ses = boto3.client("ses", region_name=os.environ.get("AWS_REGION", "ap-northeast-1"),
                           config=Config(connect_timeout=3, read_timeout=8, retries={"total_max_attempts": 1}))
        result = ses.send_email(
            Source=sender, Destination={"ToAddresses": [to]},
            Message={"Subject": {"Data": subject.strip(), "Charset": "UTF-8"},
                     "Body": {"Text": {"Data": body, "Charset": "UTF-8"}}})
        return reply(200, {"message": "SESが送信を受け付けました。受信箱も確認してください。", "messageId": result["MessageId"]})
    except ClientError as error:
        code = error.response.get("Error", {}).get("Code", "Unknown")
        print(json.dumps({"event": "ses_error", "code": code, "requestId": getattr(context, "aws_request_id", "")}))
        return reply(502, {"message": "SES呼び出しでエラーが発生しました。再送前に受信箱とログを確認してください。", "code": code})
    except BotoCoreError:
        print(json.dumps({"event": "ses_connection_error", "requestId": getattr(context, "aws_request_id", "")}))
        return reply(502, {"message": "送信結果が不明です。重複送信を避けるため、受信箱を確認してから判断してください。"})
