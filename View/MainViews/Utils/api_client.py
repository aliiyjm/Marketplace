"""HTTP client for the users routes of the API Gateway (Lambda + DynamoDB MarketplaceDB).

The API Gateway URL comes ONLY from API_URL in .env (it is never written in
the code). Every request carries a new JWT service token (valid 60 s) signed
with MARKETPLACE_API_KEY; the secret itself never travels. Without a valid
token the Lambda answers 401.
"""
import logging
import os

import requests

from Interfaces.ServiceToken import auth_header

log = logging.getLogger("api_client")

# The "Invoke URL" of your API Gateway (AWS console -> API Gateway -> your API ->
# Stages), set as API_URL in .env. No trailing "/" and no "/usuarios/...".
API_URL = os.environ.get("API_URL", "").rstrip("/")
TIMEOUT = 8  # seconds; avoids hanging forever if AWS does not answer
API_KEY = os.environ.get("MARKETPLACE_API_KEY", "")

# Route names exactly as they are configured in API Gateway and the Lambda.
ROUTE_QUERY = "/usuarios/consultar"
ROUTE_ADD = "/usuarios/agregar"
ROUTE_UPDATE = "/usuarios/modificar"
ROUTE_DELETE = "/usuarios/eliminar"


class ApiError(Exception):
    """Error that is safe to show to the user (no internal details)."""


class DuplicateId(ApiError):
    """The Lambda answered 409: a user with that ID already exists."""


def _failure(action, status):
    """Readable error including the HTTP status, to know what AWS answered."""
    hint = ""
    if status in (401, 403):
        hint = " Check that MARKETPLACE_API_KEY matches the Lambda."
    elif status == 404:
        hint = " Check that API_URL in .env is your API Gateway URL."
    return ApiError(f"{action} (HTTP {status}).{hint}")


def _clean(user):
    """boto3 rejects floats, but the Lambda returns 7771452085.0 and sending it
    back as-is would fail. Convert whole-number floats to int.
    También renombra la llave 'id' a 'id_user' automáticamente para compatibilidad con la nueva tabla."""
    cleaned = {}
    for k, v in user.items():
        # Si el resto de la interfaz aún manda "id", esto lo corrige a "id_user"
        new_key = "id_user" if k == "id" else k
        cleaned[new_key] = int(v) if isinstance(v, float) and v.is_integer() else v
    return cleaned


def _request(method, route, **kwargs):
    if not API_URL:
        raise ApiError("API_URL is not configured: set it in .env (API Gateway invoke URL).")
    try:
        resp = requests.request(method, API_URL + route, timeout=TIMEOUT,
                                headers=auth_header(API_KEY), **kwargs)
    except requests.RequestException:
        raise ApiError("Could not connect to the data server.") from None
    try:
        data = resp.json()
    except ValueError:
        data = {}
    if resp.status_code >= 400:
        # Server terminal only (never shown to the user).
        log.warning("%s %s -> %s %s", method, route, resp.status_code, resp.text[:200])
    return resp.status_code, data


def list_users():
    status, data = _request("GET", ROUTE_QUERY)
    if status != 200 or not isinstance(data, list):
        raise _failure("Could not load the user list", status)
    return data


def get_user(user_id):
    """Return the full user, or None if it does not exist."""
    # Cambiado a 'id_user' para coincidir con la Lambda y DynamoDB
    status, data = _request("GET", ROUTE_QUERY, params={"id_user": user_id})
    if status == 404:
        return None
    if status != 200 or not isinstance(data, dict):
        raise _failure("Could not fetch the user", status)
    return data


def get_user_by_email(email):
    """Sign-in lookup: the Lambda uses the GSI1 index (EMAIL#...), no table scan."""
    status, data = _request("GET", ROUTE_QUERY, params={"email": email})
    if status == 404:
        return None
    if status != 200 or not isinstance(data, dict):
        raise _failure("Could not fetch the user", status)
    return data


def add_user(user):
    status, data = _request("POST", ROUTE_ADD, json=_clean(user))
    if status == 409 and isinstance(data, dict) and data.get("code") == "EMAIL_EXISTS":
        raise ApiError("That email is already registered.")
    if status == 409:  # the Lambda rejects repeated IDs
        raise DuplicateId("A user with that ID already exists.")
    if status != 200:
        raise _failure("Could not add the user", status)


def update_user(user):
    status, data = _request("PUT", ROUTE_UPDATE, json=_clean(user))
    if status == 409:
        raise ApiError("That email is already registered.")
    if status != 200:
        raise _failure("Could not save the user", status)


def delete_user(user_id):
    # Cambiado a 'id_user' para coincidir con la Lambda y DynamoDB
    status, _ = _request("DELETE", ROUTE_DELETE, params={"id_user": user_id})
    if status != 200:
        raise _failure("Could not delete the user", status)