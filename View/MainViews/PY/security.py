"""Security for the web layer.

- CSRF token on every POST form (compared in constant time).
- Strict Content-Security-Policy: no inline scripts or styles, so injected
  HTML cannot run code. That is why templates use CSS classes only.
- Session user is reloaded on every request (deleted / demoted users lose
  access immediately).
- login_required / seller_required / admin_required decorators.
- safe_next() blocks open redirects in ?next=.
"""
import hmac
import secrets
from functools import wraps
from urllib.parse import urlsplit

from flask import (abort, current_app, flash, g, redirect, request, session,
                   url_for)

CSP = ("default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com; img-src 'self' data:; object-src 'none'; "
       "base-uri 'none'; frame-ancestors 'none'; form-action 'self'")


def services():
    return current_app.extensions["services"]


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


def init_security(app):
    app.jinja_env.globals["csrf_token"] = csrf_token

    @app.before_request
    def load_user_and_check_csrf():
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
        resp.headers["Content-Security-Policy"] = CSP
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
