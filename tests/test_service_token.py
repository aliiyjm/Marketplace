"""JWT service tokens (Interfaces/ServiceToken.py): what the Lambda accepts and
the classic attacks it must refuse."""
import base64
import hashlib
import hmac
import json

import pytest

from Interfaces.ServiceToken import (AUDIENCE, ISSUER, MAX_LIFETIME, TokenError, auth_header,
                                     bearer_token, sign_token, verify_token)

SECRET = "a-long-shared-secret-for-tests"
NOW = 1_800_000_000


def b64(data):
    return base64.urlsafe_b64encode(json.dumps(data, separators=(",", ":")).encode()).rstrip(b"=").decode()


def forge(header, claims, secret=SECRET):
    """A token with any header/claims, correctly signed with `secret` (to test the claim checks)."""
    signing_input = b64(header) + "." + b64(claims)
    sig = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return signing_input + "." + base64.urlsafe_b64encode(sig).rstrip(b"=").decode()


def good_claims(**over):
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": "flask-app", "scope": "read write",
              "iat": NOW, "exp": NOW + 60, "jti": "abc123"}
    claims.update(over)
    return claims


def test_round_trip():
    token = sign_token(SECRET, "read", now=NOW)
    claims = verify_token(token, SECRET, now=NOW + 10)
    assert claims["scope"] == "read" and claims["exp"] - claims["iat"] == 60
    assert claims["iss"] == ISSUER and claims["aud"] == AUDIENCE and len(claims["jti"]) == 24
    assert sign_token(SECRET, now=NOW) != sign_token(SECRET, now=NOW)        # unique jti


def test_expired_token_is_refused_after_the_clock_skew():
    token = sign_token(SECRET, now=NOW)
    verify_token(token, SECRET, now=NOW + 60 + 29)                           # within the 30 s skew
    with pytest.raises(TokenError, match="expired"):
        verify_token(token, SECRET, now=NOW + 60 + 31)


@pytest.mark.parametrize("header", [
    {"alg": "none", "typ": "JWT"},
    {"alg": "HS512", "typ": "JWT"},
    {"alg": "RS256", "typ": "JWT"},
    {"typ": "JWT"},
])
def test_only_hs256_is_accepted(header):
    with pytest.raises(TokenError, match="algorithm"):
        verify_token(forge(header, good_claims()), SECRET, now=NOW)


def test_alg_none_without_signature_is_refused():
    token = b64({"alg": "none", "typ": "JWT"}) + "." + b64(good_claims()) + "."
    with pytest.raises(TokenError):
        verify_token(token, SECRET, now=NOW)


def test_tampered_permissions_break_the_signature():
    header_b64, _, sig = sign_token(SECRET, "read", now=NOW).split(".")
    upgraded = header_b64 + "." + b64(good_claims(scope="read write")) + "." + sig
    with pytest.raises(TokenError, match="signature"):
        verify_token(upgraded, SECRET, now=NOW)


def test_token_signed_with_another_secret_is_refused():
    with pytest.raises(TokenError, match="signature"):
        verify_token(sign_token("someone-elses-secret", now=NOW), SECRET, now=NOW)


@pytest.mark.parametrize("claims, reason", [
    (good_claims(iss="evil"), "issuer"),
    (good_claims(aud="another-api"), "audience"),
    (good_claims(iat=NOW + 120, exp=NOW + 180), "times"),                     # issued in the future
    (good_claims(exp=NOW + MAX_LIFETIME + 10), "times"),                      # lives too long
    (good_claims(scope="admin"), "scope"),
    (good_claims(scope=""), "scope"),
    (good_claims(iat="now"), "times"),
    (good_claims(jti=None), "token id"),
])
def test_claims_are_checked(claims, reason):
    with pytest.raises(TokenError, match=reason):
        verify_token(forge({"alg": "HS256", "typ": "JWT"}, claims), SECRET, now=NOW)


@pytest.mark.parametrize("token", ["", "abc", "a.b", "a.b.c", "x" * 5000, None, 123])
def test_malformed_tokens(token):
    with pytest.raises(TokenError):
        verify_token(token, SECRET, now=NOW)


def test_no_secret_no_tokens():
    with pytest.raises(TokenError):
        sign_token("")
    with pytest.raises(TokenError):
        verify_token(sign_token(SECRET), "")
    with pytest.raises(TokenError):
        sign_token(SECRET, scope="root")
    assert auth_header("") == {}


def test_bearer_header_parsing():
    assert bearer_token({"authorization": "Bearer abc.def.ghi"}) == "abc.def.ghi"
    assert bearer_token({"authorization": "bearer abc"}) == "abc"
    assert bearer_token({"authorization": "Basic abc"}) == ""
    assert bearer_token({}) == "" and bearer_token(None) == ""


def test_the_secret_never_travels():
    from View.MainViews.Utils.product_api import ProductApiClient

    class Recorder:
        def __init__(self):
            self.headers = None

        def request(self, method, url, headers=None, **kwargs):
            self.headers = headers

            class R:
                status_code = 200

                @staticmethod
                def json():
                    return {"items": [], "next_cursor": None}
            return R()
    http = Recorder()
    ProductApiClient(base_url="https://api.example.test", api_key=SECRET, http=http).list_page()
    assert set(http.headers) == {"Authorization"} and http.headers["Authorization"].startswith("Bearer ")
    assert SECRET not in json.dumps(http.headers)
    assert verify_token(bearer_token({"authorization": http.headers["Authorization"]}), SECRET)["scope"] == "read write"


def test_api_url_is_never_hardcoded(monkeypatch):
    from View.MainViews.Utils import api_client
    from View.MainViews.Utils.product_api import ProductApiClient
    monkeypatch.setattr(api_client, "API_URL", "")
    with pytest.raises(api_client.ApiError, match="API_URL"):
        api_client.list_users()
    with pytest.raises(RuntimeError, match="API_URL"):
        ProductApiClient(api_key=SECRET)
    source = open(api_client.__file__, encoding="utf-8").read()
    assert "execute-api" not in source
