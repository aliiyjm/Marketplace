"""Shared fixtures. Each test gets its own fresh data (1,200 products) so tests
never depend on each other."""
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from Services.ServiceContainer import build_services  # noqa: E402
from View.MainViews.PY.app import create_app  # noqa: E402

ANA = "u-ana00001"
CARLOS_SELLER = "SEL-0001"
PASSWORD = "Demo1234"


@pytest.fixture
def services():
    return build_services(product_count=1200, backend="memory")


@pytest.fixture
def app(services):
    return create_app(services, config={"TESTING": True, "SECRET_KEY": "test"})


@pytest.fixture
def client(app):
    return app.test_client()


def csrf_from(html):
    match = re.search(r'name="csrf_token" value="([0-9a-f]+)"', html)
    assert match, "page has no CSRF token"
    return match.group(1)


def token(client, url="/account/access"):
    return csrf_from(client.get(url).get_data(as_text=True))


def login(client, email, password=PASSWORD):
    resp = client.post("/account/signin", data={"csrf_token": token(client), "email": email, "password": password})
    assert resp.status_code == 302, resp.get_data(as_text=True)[:500]
    return resp


def some_product(services, in_stock=True, seller_id=None):
    for p in services.store.products.values():
        if p["status"] == "active" and (p["stock"] > 3 if in_stock else True) \
                and (seller_id is None or p["seller_id"] == seller_id):
            return p
    raise AssertionError("no product found")
