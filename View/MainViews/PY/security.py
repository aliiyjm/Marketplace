"""Security for the web layer.

- CSRF token on every POST form (compared in constant time).
- Strict Content-Security-Policy: no inline scripts or styles, so injected
  HTML cannot run code. That is why templates use CSS classes only.
- Session user is reloaded on every request (deleted / demoted users lose
  access immediately).
- login_required / seller_required / admin_required decorators.
- safe_next() blocks open redirects in ?next=.
- Request size: 1 MB everywhere; only the seller product form accepts a photo
  (UPLOAD_LIMIT), and only for signed-in users, so anonymous clients cannot
  push big bodies.
- Product photos: img-src only allows this site and the S3 bucket given in
  PRODUCT_IMAGES_URL (https origin, nothing else).
"""
import hmac
import logging
import os
import re
import secrets
from functools import wraps
from urllib.parse import urlsplit

from flask import (abort, current_app, flash, g, redirect, request, session,
                   url_for)

log = logging.getLogger("security")
CSP_TEMPLATE = ("default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
                "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:{images}; object-src 'none'; "
                "base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
CSP = CSP_TEMPLATE.format(images="")
IMAGES_ORIGIN_RE = re.compile(r"https://[a-z0-9][a-z0-9-]{1,61}[a-z0-9]\.s3\.[a-z0-9-]+\.amazonaws\.com")
UPLOAD_ENDPOINTS = {"seller.new_product", "seller.edit_product"}
UPLOAD_LIMIT = 6 * 1024 * 1024        # 5 MB photo + the rest of the form


def images_origin(value=None):
    """PRODUCT_IMAGES_URL from .env -> 'https://<bucket>.s3.<region>.amazonaws.com', or ''.
    Anything else (http, a path, another domain) is ignored with a warning."""
    value = (os.environ.get("PRODUCT_IMAGES_URL", "") if value is None else value).strip().rstrip("/")
    if not value:
        return ""
    if not IMAGES_ORIGIN_RE.fullmatch(value):
        log.warning("PRODUCT_IMAGES_URL ignored: it must look like https://<bucket>.s3.<region>.amazonaws.com")
        return ""
    return value


def build_csp(origin=""):
    return CSP_TEMPLATE.format(images=f" {origin}" if origin else "")


def services():
    return current_app.extensions["services"]


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


def init_security(app):
    app.jinja_env.globals["csrf_token"] = csrf_token
    csp = build_csp(images_origin(app.config.get("PRODUCT_IMAGES_URL")))   # None -> read .env

    @app.before_request
    def load_user_and_check_csrf():
        if request.endpoint in UPLOAD_ENDPOINTS and request.method == "POST" and session.get("uid"):
            request.max_content_length = UPLOAD_LIMIT     # must be set before the form is parsed
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            sent = request.form.get("csrf_token", "").encode()
            expected = session.get("csrf", "").encode()
            if not expected or not hmac.compare_digest(sent, expected):
                abort(400)
        g.user = None
        uid = session.get("uid")
        if uid:
            g.user = services().auth.current_user(uid)
            if g.user is None:          # the account was deleted meanwhile
                session.clear()

    @app.after_request
    def security_headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        resp.headers["Content-Security-Policy"] = csp
        if g.get("user"):
            # Pages with personal data must not be stored by shared caches/browsers.
            resp.headers["Cache-Control"] = "no-store"
        return resp


def safe_next(target, default=None):
    """Only allow relative paths of this site (no //evil.com, no http://...)."""
    if target:
        parts = urlsplit(target)
        if (not parts.scheme and not parts.netloc and target.startswith("/")
                and not target.startswith("//") and "\\" not in target):
            return target
    return default or url_for("shop.home")


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if g.user is None:
            flash("Please sign in to continue.", "info")
            nxt = request.full_path if request.method == "GET" else request.referrer
            return redirect(url_for("auth.access", next=safe_next(nxt and urlsplit(nxt).path or None, "/")))
        return view(*args, **kwargs)
    return wrapper


def seller_required(view):
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if not g.user.seller_id:
            abort(403)
        return view(*args, **kwargs)
    return wrapper


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapper(*args, **kwargs):
        if not g.user.is_admin:
            abort(403)
        return view(*args, **kwargs)
    return wrapper


def flash_errors(error):
    for message in getattr(error, "errors", [str(error)]):
        flash(message, "error")
