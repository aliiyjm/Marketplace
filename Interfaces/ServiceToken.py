"""Service tokens: JWT (RFC 7519) signed with HMAC-SHA256, between the Flask app
and the Lambda behind API Gateway.

    Flask  --- Authorization: Bearer <JWT, 60 s> --->  API Gateway -> Lambda

- The secret (MARKETPLACE_API_KEY) never travels: only a token signed with it.
- A token lives 60 seconds: a copied token is useless a minute later.
- Permissions (RBAC) go in the `scope` claim: "read" (GET routes) and/or
  "write" (routes that change data). The Flask backend uses "read write";
  scripts/make_token.py can create a read-only token for demos/Postman.
- Verification is strict: only alg HS256 (never "none" or another algorithm),
  constant-time signature check, issuer, audience, expiry, issue time and a
  maximum lifetime.

Only the standard library is used, so the Lambda needs no extra package.
"""
import base64
import hashlib
import hmac
import json
import secrets
import time

ALGORITHM = "HS256"
ISSUER = "straw-denim-web"
AUDIENCE = "straw-denim-api"
TOKEN_LIFETIME = 60          # seconds: tokens made by the Flask app for each request
MAX_LIFETIME = 3600          # longest token accepted (scripts/make_token.py, for Postman)
CLOCK_SKEW = 30              # seconds of clock difference tolerated between machines
SCOPES = ("read", "write")
MAX_TOKEN_LENGTH = 2048


class TokenError(Exception):
    """The token is missing, malformed, expired or has a bad signature."""


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text):
    try:
        return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))
    except (ValueError, TypeError):
        raise TokenError("malformed token") from None


def _signature(secret, signing_input):
    return hmac.new(secret.encode("utf-8"), signing_input.encode("ascii"), hashlib.sha256).digest()


def sign_token(secret, scope="read write", subject="flask-app", lifetime=TOKEN_LIFETIME, now=None):
    """A new JWT signed with `secret`."""
    if not secret:
        raise TokenError("no secret configured")
    wanted = scope.split()
    if not wanted or any(s not in SCOPES for s in wanted):
        raise TokenError("unknown scope")
    if not 1 <= lifetime <= MAX_LIFETIME:
        raise TokenError("invalid lifetime")
    issued = int(time.time() if now is None else now)
    header = {"alg": ALGORITHM, "typ": "JWT"}
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": subject, "scope": " ".join(wanted),
              "iat": issued, "exp": issued + lifetime, "jti": secrets.token_hex(12)}
    signing_input = (_b64(json.dumps(header, separators=(",", ":")).encode())
                     + "." + _b64(json.dumps(claims, separators=(",", ":")).encode()))
    return signing_input + "." + _b64(_signature(secret, signing_input))


def verify_token(token, secret, now=None):
    """Claims of a valid token; TokenError (short reason) otherwise."""
    if not secret:
        raise TokenError("no secret configured")
    if not isinstance(token, str) or not token or len(token) > MAX_TOKEN_LENGTH:
        raise TokenError("missing or oversized token")
    parts = token.split(".")
    if len(parts) != 3:
        raise TokenError("malformed token")
    header_b64, claims_b64, signature_b64 = parts
    try:
        header = json.loads(_unb64(header_b64))
        claims = json.loads(_unb64(claims_b64))
    except (ValueError, UnicodeDecodeError):
        raise TokenError("malformed token") from None
    if not isinstance(header, dict) or header.get("alg") != ALGORITHM or header.get("typ", "JWT") != "JWT":
        raise TokenError("algorithm not allowed")            # blocks alg=none and algorithm confusion
    expected = _signature(secret, header_b64 + "." + claims_b64)
    if not hmac.compare_digest(expected, _unb64(signature_b64)):
        raise TokenError("bad signature")
    if not isinstance(claims, dict):
        raise TokenError("malformed token")
    current = int(time.time() if now is None else now)
    iat, exp = claims.get("iat"), claims.get("exp")
    if not all(isinstance(v, int) and not isinstance(v, bool) for v in (iat, exp)):
        raise TokenError("missing times")
    if claims.get("iss") != ISSUER or claims.get("aud") != AUDIENCE:
        raise TokenError("wrong issuer or audience")
    if exp <= current - CLOCK_SKEW:
        raise TokenError("expired")
    if iat > current + CLOCK_SKEW or exp - iat > MAX_LIFETIME or exp <= iat:
        raise TokenError("invalid times")
    scope = claims.get("scope")
    if not isinstance(scope, str) or not scope.split() or any(s not in SCOPES for s in scope.split()):
        raise TokenError("invalid scope")
    if not isinstance(claims.get("jti"), str):
        raise TokenError("missing token id")
    return claims


def bearer_token(headers):
    """Token of an `Authorization: Bearer <token>` header (headers already lowercased), or ''."""
    value = str((headers or {}).get("authorization", ""))
    kind, _, token = value.partition(" ")
    return token.strip() if kind.lower() == "bearer" else ""


def auth_header(secret, scope="read write"):
    """{"Authorization": "Bearer <new token>"} for an outgoing request ({} without a secret)."""
    return {"Authorization": "Bearer " + sign_token(secret, scope)} if secret else {}
