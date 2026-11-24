"""Helpers shared by every Lambda route module (users and products).

Environment variables of the Lambda:
    MARKETPLACE_TABLE    DynamoDB table (default MarketplaceDB)
    MARKETPLACE_GSI      global secondary index (default GSI1)
    MARKETPLACE_API_KEY  secret shared with the Flask app (header x-api-key)
"""
import base64
import hmac
import json
import logging
import os

from Interfaces.Validators import ValidationError

log = logging.getLogger()
log.setLevel(logging.INFO)
MAX_BODY = 1_000_000
_table = None


def get_table():
    """The single MarketplaceDB table (boto3 comes with the Lambda runtime)."""
    global _table
    if _table is None:
        import boto3
        _table = boto3.resource("dynamodb").Table(os.environ.get("MARKETPLACE_TABLE", "MarketplaceDB"))
    return _table


def gsi_name():
    return os.environ.get("MARKETPLACE_GSI", "GSI1")


def response(status, body):
    return {"statusCode": status, "headers": {"Content-Type": "application/json"},
            "body": json.dumps(body, ensure_ascii=False)}


def parse_request(event):
    """Supports API Gateway HTTP API (v2) and REST API (v1) events."""
    http = (event.get("requestContext") or {}).get("http") or {}
    method = (http.get("method") or event.get("httpMethod") or "").upper()
    path = event.get("rawPath") or event.get("path") or ""
    headers = {str(k).lower(): v for k, v in (event.get("headers") or {}).items()}
    query = event.get("queryStringParameters") or {}
    body = event.get("body") or ""
    if event.get("isBase64Encoded") and body:
        body = base64.b64decode(body).decode("utf-8")
    return method, path.rstrip("/"), headers, query, body


def json_body(body):
    if len(body) > MAX_BODY:
        raise ValidationError("Body too large.")
    try:
        data = json.loads(body or "{}")
    except ValueError:
        raise ValidationError("Body must be valid JSON.") from None
    if not isinstance(data, dict):
        raise ValidationError("Body must be a JSON object.")
    return data


def api_key_configured():
    return bool(os.environ.get("MARKETPLACE_API_KEY"))


def authorized(headers):
    """Constant-time comparison of the x-api-key header with the Lambda secret."""
    expected = os.environ.get("MARKETPLACE_API_KEY", "")
    sent = str(headers.get("x-api-key", ""))
    return bool(expected) and hmac.compare_digest(sent.encode(), expected.encode())
