"""HTTP-level tests: every screen renders, and the security rules hold."""
import re

import pytest

from tests.conftest import ANA, csrf_from, login, some_product, token

PUBLIC = ["/", "/?q=jeans&cat=women&sort=price_desc&page=2", "/product/PRD-000004", "/account/access"]
BUYER = ["/cart", "/orders", "/orders?status=Delivered", "/favorites"]
SELLER = ["/seller", "/seller/products", "/seller/products?status=out", "/seller/products/new"]
ADMIN = ["/admin/users", "/admin/users/new", "/admin/categories", "/admin/orders", "/admin/categories/11/edit"]


def page(client, url):
    resp = client.get(url)
    assert resp.status_code == 200, f"{url} -> {resp.status_code}"
    return resp.get_data(as_text=True)


# ---------- rendering ----------
@pytest.mark.parametrize("url", PUBLIC)
def test_public_pages_render(client, url):
    page(client, url)


def test_buyer_pages_render(client, services):
    login(client, "ana@example.com")
    for url in BUYER:
        page(client, url)
    order_id = max(o["order_id"] for o in services.store.orders.values() if o["buyer_id"] == ANA)
    for url in (f"/orders/{order_id}", f"/orders/{order_id}/confirmation", f"/orders/{order_id}/invoice"):
        page(client, url)


def test_seller_pages_render(client, services):
    login(client, "carlos@urbanthread.mx")
    for url in SELLER:
        page(client, url)
    pid = some_product(services, seller_id="SEL-0001")["product_id"]
    page(client, f"/seller/products/{pid}/edit")
    page(client, f"/seller/products/{pid}/delete")


def test_admin_pages_render(client, services):
    login(client, "admin@marketplace.mx")
    for url in ADMIN + [f"/admin/orders/{max(services.store.orders)}", f"/admin/users/{ANA}/edit"]:
        page(client, url)


def test_unknown_product_is_404(client):
    assert client.get("/product/PRD-999999").status_code == 404
    assert client.get("/product/../../etc/passwd").status_code == 404


# ---------- security ----------
def test_security_headers(client):
    resp = client.get("/")
    csp = resp.headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "frame-ancestors 'none'" in csp
    assert resp.headers["X-Frame-Options"] == "DENY"
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def _no_inline_code(html, url):
    assert 'style="' not in html, url
    assert not re.search(r"<script(?![^>]*\bsrc=)", html), url
    assert " onclick=" not in html.lower(), url


def test_templates_have_no_inline_styles_or_scripts(client):
    for url in PUBLIC:
        _no_inline_code(page(client, url), url)
    login(client, "ana@example.com")
    for url in BUYER:
        html = page(client, url)
        _no_inline_code(html, url)


def test_post_without_csrf_is_rejected(client):
    login(client, "ana@example.com")
    resp = client.post("/cart/add", data={"product_id": "PRD-000004", "size": "M", "color": "Red"})
    assert resp.status_code == 400


def test_private_pages_require_login(client):
    for url in BUYER + ["/checkout", "/seller", "/admin/users"]:
        resp = client.get(url)
        assert resp.status_code == 302 and "/account/access" in resp.location, url


def test_roles_are_enforced(client):
    login(client, "ana@example.com")
    assert client.get("/seller").status_code == 403
    assert client.get("/admin/users").status_code == 403


def test_buyer_cannot_open_other_buyers_order(client, services):
    other_id = services.store.orders and max(services.store.orders)
    services.store.orders[other_id]["buyer_id"] = "u-buyer0001"
    login(client, "ana@example.com")
    assert client.get(f"/orders/{other_id}").status_code == 404
    assert client.get(f"/orders/{other_id}/invoice").status_code == 404


def test_search_output_is_escaped(client):
    html = page(client, "/?q=%3Cscript%3Ealert(1)%3C/script%3E")
    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;" in html


def test_open_redirect_is_blocked(client):
    resp = client.post("/account/signin", data={"csrf_token": token(client), "email": "ana@example.com",
                                                "password": "Demo1234", "next": "//evil.example.com/x"})
    assert resp.status_code == 302 and resp.location in ("/", "http://localhost/")


def test_wrong_password_shows_generic_error(client):
    resp = client.post("/account/signin", data={"csrf_token": token(client), "email": "ana@example.com",
                                                "password": "nope"})
    assert resp.status_code == 401
    assert "Email or password is incorrect." in resp.get_data(as_text=True)


def test_session_is_renewed_on_login(client):
    before = token(client)
    login(client, "ana@example.com")
    assert token(client, "/cart") != before


def test_seller_cannot_edit_other_sellers_product_by_url(client, services):
    other = some_product(services, seller_id="SEL-0005")
    login(client, "carlos@urbanthread.mx")
    assert client.get(f"/seller/products/{other['product_id']}/edit").status_code == 404
    tok = token(client, "/seller")
    resp = client.post(f"/seller/products/{other['product_id']}/delete", data={"csrf_token": tok})
    assert resp.status_code == 404 and other["status"] == "active"


def test_admin_cannot_delete_or_demote_self(client, services):
    login(client, "admin@marketplace.mx")
    tok = token(client, "/admin/users")
    client.post("/admin/users/u-admin0001/delete", data={"csrf_token": tok})
    assert "u-admin0001" in services.store.users
    client.post("/admin/users/u-admin0001/edit", data={"csrf_token": tok, "name": "Diego", "email": "admin@marketplace.mx", "role": "buyer"})
    assert services.store.users["u-admin0001"]["role"] == "admin"


# ---------- complete flows ----------
def test_buy_flow_end_to_end(client, services):
    login(client, "ana@example.com")
    p = some_product(services)
    html = page(client, f"/product/{p['product_id']}")
    tok = csrf_from(html)
    resp = client.post("/cart/add", data={"csrf_token": tok, "product_id": p["product_id"], "size": p["sizes"][0],
                                          "color": p["colors"][0], "qty": "2"})
    assert resp.status_code == 302
    assert "Order summary" in page(client, "/cart")
    page(client, "/checkout")
    resp = client.post("/checkout", data={"csrf_token": tok, "street": "Av. Morelos 5", "city": "Cuernavaca",
                                          "state": "Morelos", "zip_code": "62000", "method": "card",
                                          "card_number": "4242424242424242", "expiry": "12/30", "cvv": "123"})
    assert resp.status_code == 302 and "/confirmation" in resp.location
    html = page(client, resp.location)
    assert "your order is confirmed" in html
    assert "4242424242424242" not in html


def test_checkout_errors_do_not_echo_card_data(client, services):
    login(client, "ana@example.com")
    p = some_product(services)
    tok = token(client, f"/product/{p['product_id']}")
    client.post("/cart/add", data={"csrf_token": tok, "product_id": p["product_id"], "size": p["sizes"][0],
                                   "color": p["colors"][0], "qty": "1"})
    resp = client.post("/checkout", data={"csrf_token": tok, "street": "X 1", "city": "Y", "state": "Morelos",
                                          "zip_code": "62000", "method": "card", "card_number": "4111111111111112",
                                          "expiry": "12/30", "cvv": "999"})
    html = resp.get_data(as_text=True)
    assert "Enter a valid card number." in html
    assert "4111111111111112" not in html and 'value="999"' not in html


def test_admin_moves_an_order_forward(client, services):
    order_id = next(o["order_id"] for o in services.store.orders.values() if o["status"] == "Paid")
    login(client, "admin@marketplace.mx")
    tok = token(client, "/admin/orders")
    client.post(f"/admin/orders/{order_id}/status", data={"csrf_token": tok, "new_status": "Shipped"})
    assert services.store.orders[order_id]["status"] == "Shipped"
    assert services.store.status_history[order_id][-1]["old_status"] == "Paid"


def test_favorites_toggle(client, services):
    login(client, "ana@example.com")
    p = some_product(services)
    tok = token(client, "/favorites")
    client.post("/favorites/toggle", data={"csrf_token": tok, "product_id": p["product_id"], "next": "/favorites"})
    assert p["name"] in page(client, "/favorites")


def test_register_then_seller_center(client):
    resp = client.post("/account/register", data={
        "csrf_token": token(client), "name": "Rosa Pérez", "email": "rosa@moda.mx", "password": "Moda2026",
        "confirm": "Moda2026", "role": "seller", "store_name": "Rosa Moda", "tax_id": "PERR850101AB1", "terms": "1"})
    assert resp.status_code == 302 and resp.location.endswith("/seller")
    assert "Hello, Rosa Moda" in page(client, "/seller")


def test_store_logo_is_served_and_used(client):
    html = client.get("/").get_data(as_text=True)
    assert 'class="brand__logo"' in html and 'rel="icon" type="image/png"' in html and "Straw Denim" in html
    for name in ("logo.png", "logo@2x.png", "logo@4x.png", "logo-192.png"):
        resp = client.get(f"/static/img/{name}")
        assert resp.status_code == 200 and resp.mimetype == "image/png"
    assert client.get("/static/img/../PY/app.py").status_code == 404
    assert client.get("/static/img/%2e%2e/PY/app.py").status_code == 404
