"""Helpers shared by every Lambda route module (users and products).

Environment variables of the Lambda:
    MARKETPLACE_TABLE    DynamoDB table (default MarketplaceDB)
    MARKETPLACE_GSI      global secondary index (default GSI1)
    MARKETPLACE_API_KEY  secret shared with the Flask app: it SIGNS the JWT service tokens
                         (Authorization: Bearer ...); the secret itself never travels
    MARKETPLACE_BUCKET   S3 bucket for product photos (optional: without it photos are disabled)
"""
import base64
import json
import logging
import os

from Interfaces.ServiceToken import TokenError, bearer_token, verify_token
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


def json_body(body, max_size=MAX_BODY):
    if len(body) > max_size:
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


def token_claims(headers):
    """Claims of a valid JWT in Authorization: Bearer, or None (reason only in CloudWatch)."""
    secret = os.environ.get("MARKETPLACE_API_KEY", "")
    token = bearer_token(headers)
    if not secret or not token:
        return None
    try:
        return verify_token(token, secret)
    except TokenError as e:
        log.info("Rejected token: %s", e)
        return None


def authorized(headers, scope="write"):
    """True when the request carries a valid token WITH that permission."""
    claims = token_claims(headers)
    return bool(claims) and scope in claims["scope"].split()


def access_denied(headers, scope):
    """None if the request may go on; otherwise the error response.
    503 = the Lambda has no secret (fail closed), 401 = no valid token,
    403 = valid token without the permission the route needs (RBAC)."""
    if not api_key_configured():
        return response(503, {"error": "MARKETPLACE_API_KEY is not configured in the Lambda."})
    claims = token_claims(headers)
    if claims is None:
        return response(401, {"error": "Missing or invalid token."})
    if scope not in claims["scope"].split():
        return response(403, {"error": "Forbidden: the token lacks the '" + scope + "' permission."})
    return None
