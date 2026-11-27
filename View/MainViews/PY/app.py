"""Flask application (Python 3.13): clothing marketplace.

Run from the project root:   python View/MainViews/PY/app.py
Then open http://127.0.0.1:5000

Layers: View (this folder) -> Services -> Interfaces -> Implementation.
The View never touches data directly; it only calls Services.
"""
import logging
import os
import secrets
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]       # project root
BASE = Path(__file__).resolve().parents[1]       # View/MainViews
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Database settings and keys come from .env (before any module reads them).
from View.MainViews.Utils.env_file import load_env_file  # noqa: E402

load_env_file()

from flask import Flask, g, render_template, send_from_directory  # noqa: E402

from Interfaces.DataInterfaces.RepositoryErrors import DatabaseUnavailable  # noqa: E402
from Services.ServiceContainer import build_services  # noqa: E402
from View.MainViews.PY import (account_views, admin_views, auth_views,  # noqa: E402
                               cart_views, seller_views, shop_views)
from View.MainViews.PY.security import init_security, services  # noqa: E402

STORE_NAME = "Straw Denim"     # name shown in every page, invoice and the browser tab
STORE_MARK = "SD"              # initials in the logo square


def _money(value):
    try:
        return f"${value:,.2f}"
    except (TypeError, ValueError):
        return "—"


def _date(value, with_time=False):
    try:
        d = datetime.fromisoformat(str(value))
    except ValueError:
        return value
    text = f"{d:%b} {d.day}, {d.year}"
    return f"{text} · {d:%H:%M}" if with_time and len(str(value)) > 10 else text


def create_app(container=None, config=None):
    app = Flask(__name__, template_folder=str(BASE / "HTML"), static_folder=None)
    app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
    app.config.update(
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE") == "1",  # set 1 behind HTTPS
        PERMANENT_SESSION_LIFETIME=timedelta(days=14),
        MAX_CONTENT_LENGTH=1 * 1024 * 1024,      # forms only: 1 MB is plenty
    )
    if config:
        app.config.update(config)
    app.extensions["services"] = container or build_services()

    init_security(app)
    app.jinja_env.filters["money"] = _money
    app.jinja_env.filters["date"] = _date
    app.jinja_env.filters["datetime"] = lambda v: _date(v, with_time=True)

    @app.context_processor
    def globals_for_templates():
        svc = services()
        uid = g.user.id_user if g.get("user") else None
        return {
            "store_name": STORE_NAME,
            "store_mark": STORE_MARK,
            "current_user": g.get("user"),
            "cart_count": svc.cart_count.for_user(uid),
            "fav_ids": svc.fav.ids(uid) if uid else set(),
            "top_categories": svc.main.categories(),
        }

    # Static files: only the CSS and JS folders are served.
    @app.get("/static/css/<path:filename>")
    def static_css(filename):
        return send_from_directory(BASE / "CSS", filename)

    @app.get("/static/js/<path:filename>")
    def static_js(filename):
        return send_from_directory(BASE / "JS", filename)

    for module in (shop_views, auth_views, cart_views, account_views, seller_views, admin_views):
        app.register_blueprint(module.bp)

    messages = {400: ("Bad request", "The form expired or is invalid. Go back, reload the page and try again."),
                403: ("Access denied", "You do not have permission to open this page."),
                404: ("Page not found", "The page or product you are looking for does not exist."),
                405: ("Not allowed", "That action is not allowed here."),
                413: ("Too large", "The request is too large."),
                500: ("Something went wrong", "Please try again in a moment.")}

    def error_page(error):
        code = getattr(error, "code", 500) or 500
        title, text = messages.get(code, messages[500])
        return render_template("errors/error.html", code=code, title=title, text=text), code

    for code in messages:
        app.register_error_handler(code, error_page)

    def database_down(error):
        # A database is unreachable: friendly page instead of a crash (details are in the log).
        return render_template("errors/error.html", code=503, title="We'll be right back",
                               text=str(error) or "A database is not available right now."), 503

    app.register_error_handler(DatabaseUnavailable, database_down)
    return app


def _start_or_explain():
    """Build the app; if a database or setting is wrong, print ONE clear line
    (what failed and what to check) instead of a long traceback."""
    from Interfaces.Validators import ValidationError
    from View.MainViews.Utils.env_file import utf8_console

    utf8_console()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("sql").setLevel(logging.CRITICAL)   # its cause is printed below, once
    try:
        return create_app()
    except DatabaseUnavailable as e:
        print(f"\n✗ {e}\n  Cause: {e.detail or 'see the messages above'}")
    except (RuntimeError, ValidationError) as e:
        print(f"\n✗ {e}")
    print("  Check your .env and run:  python scripts/check_databases.py")
    sys.exit(1)


if __name__ == "__main__":
    application = _start_or_explain()
    backends = ", ".join(f"{k.split('_')[0].lower()}={os.environ.get(k, 'memory')}" for k in
                         ("DATA_BACKEND", "PRODUCTS_BACKEND", "FINANCE_BACKEND", "OPERATIONS_BACKEND"))
    print(f"{STORE_NAME} running on http://127.0.0.1:5000  ({backends})")
    application.run(host="127.0.0.1", port=5000, debug=os.environ.get("FLASK_DEBUG") == "1")
