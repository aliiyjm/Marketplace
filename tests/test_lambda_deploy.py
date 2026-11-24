"""The single Lambda (users + products in MarketplaceDB). Every test runs twice:
against the layered code (Services/LambdaRouter) and against the generated
lambda_deploy/lambda_function.py that gets pasted in AWS."""
import importlib.util
import json
import types

import pytest
import requests

from Implementation.ProductServiceImplementation.ProductServiceImplementation import \
    ProductServiceImplementation
from Implementation.UserServiceImplementation.UserDynamoImplementation import \
    UserDynamoImplementation
from scripts.build_single_file_lambda import OUT as SINGLE_FILE
from scripts.load_to_dynamo import build_payloads, build_users, upload, upload_users
from Services.LambdaRouter import lambda_handler as layered_router
from Services.ServiceContainer import build_services
from tests.fake_dynamo import FakeTable, RouterHttp
from View.MainViews.PY.app import create_app
from View.MainViews.Utils import api_client
from View.MainViews.Utils.product_api import ProductApiClient
from View.MainViews.Utils.quick_api import QuickApiClient
from Implementation.CartServiceImplementation.QuickDynamoImplementation import (CartDynamoImplementation,
                                                                                FavDynamoImplementation)

KEY = "router-key"
HASH = "scrypt:32768:8:1$salt$abcdef"


def _load_single():
    spec = importlib.util.spec_from_file_location("lambda_function_single", SINGLE_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SINGLE = _load_single()


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("MARKETPLACE_API_KEY", KEY)


@pytest.fixture(params=["layers", "single_file"])
def lam(request):
    """(handler, products impl, users impl, table) for one shared FakeTable."""
    table = FakeTable()
    if request.param == "layers":
        return (layered_router, ProductServiceImplementation(table), UserDynamoImplementation(table), table,
                CartDynamoImplementation(table), FavDynamoImplementation(table))
    return (SINGLE.lambda_handler, SINGLE.ProductServiceImplementation(table),
            SINGLE.UserDynamoImplementation(table), table,
            SINGLE.CartDynamoImplementation(table), SINGLE.FavDynamoImplementation(table))


def call(lam, method, path, query=None, body=None, key=KEY):
    handler, products, users, _, carts, favorites = lam
    event = {"rawPath": path, "requestContext": {"http": {"method": method}},
             "headers": {"x-api-key": key} if key else {}, "queryStringParameters": query,
             "body": json.dumps(body) if body is not None else None}
    out = handler(event, None, products=products, users=users, carts=carts, favorites=favorites)
    return out["statusCode"], json.loads(out["body"]) if out["body"] else None


def user(**over):
    data = {"id_user": "a1b2c3d4e5", "name": "Ana Torres", "email": "Ana@Example.com",
            "role": "buyer", "password_hash": HASH, "created_at": "2026-11-16T10:00:00"}
    data.update(over)
    return data


# ---------- users ----------
def test_users_routes_require_the_key(lam, monkeypatch):
    assert call(lam, "GET", "/usuarios/consultar", key=None)[0] == 403
    assert call(lam, "POST", "/usuarios/agregar", body=user(), key="nope")[0] == 403
    monkeypatch.delenv("MARKETPLACE_API_KEY")
    assert call(lam, "GET", "/usuarios/consultar")[0] == 503


def test_user_is_stored_with_single_table_keys(lam):
    assert call(lam, "POST", "/usuarios/agregar", body=user())[0] == 200
    item = lam[3].items[("USER#a1b2c3d4e5", "PROFILE")]
    assert item["GSI1PK"] == "EMAIL#ana@example.com" and item["GSI1SK"] == "USER#a1b2c3d4e5"
    status, body = call(lam, "GET", "/usuarios/consultar", {"email": "ANA@example.com"})
    assert status == 200 and body["id_user"] == "a1b2c3d4e5"
    status, body = call(lam, "GET", "/usuarios/consultar", {"id_user": "a1b2c3d4e5"})
    assert status == 200 and body["name"] == "Ana Torres"


def test_duplicate_id_and_email_are_rejected(lam):
    call(lam, "POST", "/usuarios/agregar", body=user())
    status, body = call(lam, "POST", "/usuarios/agregar", body=user(email="other@example.com"))
    assert status == 409 and body["code"] == "EXISTS"
    status, body = call(lam, "POST", "/usuarios/agregar", body=user(id_user="zzz9", email="ana@example.com"))
    assert status == 409 and body["code"] == "EMAIL_EXISTS"


@pytest.mark.parametrize("bad", [
    {"password_hash": "Demo1234"},           # plain text password
    {"role": "superadmin"}, {"email": "not-an-email"}, {"id_user": "../x"}, {"name": ""},
])
def test_invalid_users_are_rejected(lam, bad):
    assert call(lam, "POST", "/usuarios/agregar", body=user(**bad))[0] == 400
    assert not lam[3].items


def test_extra_fields_are_dropped(lam):
    call(lam, "POST", "/usuarios/agregar", body=user(is_superuser=True, PK="PRODUCT#PRD-000001"))
    item = lam[3].items[("USER#a1b2c3d4e5", "PROFILE")]
    assert "is_superuser" not in item and item["PK"] == "USER#a1b2c3d4e5"


def test_update_list_and_delete(lam):
    call(lam, "POST", "/usuarios/agregar", body=user())
    assert call(lam, "PUT", "/usuarios/modificar", body=user(name="Ana T.", role="seller"))[0] == 200
    assert call(lam, "PUT", "/usuarios/modificar", body=user(id_user="ghost"))[0] == 404
    status, body = call(lam, "GET", "/usuarios/consultar")
    assert status == 200 and body[0]["name"] == "Ana T." and body[0]["role"] == "seller"
    assert call(lam, "DELETE", "/usuarios/eliminar", {"id_user": "a1b2c3d4e5"})[0] == 200
    assert call(lam, "DELETE", "/usuarios/eliminar", {"id_user": "a1b2c3d4e5"})[0] == 404


# ---------- carts and favorites ----------
LINE = {"line_id": "0123456789ab", "product_id": "PRD-000001", "size": "M", "color": "Red", "qty": 2}


def test_cart_routes(lam):
    assert call(lam, "GET", "/carrito/consultar", {"id_user": "a1b2c3d4e5"}, key=None)[0] == 403
    status, body = call(lam, "GET", "/carrito/consultar", {"id_user": "a1b2c3d4e5"})
    assert status == 200 and body["lines"] == []
    assert call(lam, "PUT", "/carrito/guardar", body={"id_user": "a1b2c3d4e5", "lines": [LINE]})[0] == 200
    item = lam[3].items[("CART#a1b2c3d4e5", "CART")]
    assert item["lines"][0]["qty"] == 2 and "price" not in item["lines"][0]
    status, body = call(lam, "GET", "/carrito/consultar", {"id_user": "a1b2c3d4e5"})
    assert body["lines"] == [LINE]
    assert call(lam, "DELETE", "/carrito/vaciar", {"id_user": "a1b2c3d4e5"})[0] == 200
    assert ("CART#a1b2c3d4e5", "CART") not in lam[3].items


@pytest.mark.parametrize("bad", [
    {"qty": 11}, {"qty": 0}, {"qty": True}, {"line_id": "../../x"}, {"product_id": "X"}, {"size": ""},
])
def test_invalid_cart_lines_are_rejected(lam, bad):
    status, _ = call(lam, "PUT", "/carrito/guardar", body={"id_user": "a1b2c3d4e5", "lines": [dict(LINE, **bad)]})
    assert status == 400 and not lam[3].items


def test_cart_line_limit_and_extra_fields(lam):
    many = [dict(LINE, line_id=f"{i:012x}") for i in range(31)]
    assert call(lam, "PUT", "/carrito/guardar", body={"id_user": "a1b2c3d4e5", "lines": many})[0] == 400
    call(lam, "PUT", "/carrito/guardar", body={"id_user": "a1b2c3d4e5", "lines": [dict(LINE, price="0.01")]})
    assert "price" not in lam[3].items[("CART#a1b2c3d4e5", "CART")]["lines"][0]


def test_favorites_routes(lam):
    items = {"PRD-000001": "2026-11-20T10:00:00", "PRD-000002": "2026-11-21T10:00:00"}
    assert call(lam, "PUT", "/favoritos/guardar", body={"id_user": "a1b2c3d4e5", "items": items}, key=None)[0] == 403
    assert call(lam, "PUT", "/favoritos/guardar", body={"id_user": "a1b2c3d4e5", "items": items})[0] == 200
    assert lam[3].items[("FAV#a1b2c3d4e5", "FAV")]["items"] == items
    assert call(lam, "GET", "/favoritos/consultar", {"id_user": "a1b2c3d4e5"})[1]["items"] == items
    assert call(lam, "PUT", "/favoritos/guardar", body={"id_user": "a1b2c3d4e5", "items": {"bad": "x"}})[0] == 400


# ---------- users + products in the same table ----------
def test_users_and_products_share_the_table_without_mixing(lam):
    payload = build_payloads(3)[0]
    call(lam, "POST", "/usuarios/agregar", body=user())
    assert call(lam, "POST", "/productos/agregar", body=payload)[0] == 201
    assert len(lam[3].items) == 2
    status, body = call(lam, "GET", "/productos/consultar", {"limit": "10"}, key=None)
    assert [p["product_id"] for p in body["items"]] == [payload["product_id"]]
    status, body = call(lam, "GET", "/usuarios/consultar")
    assert [u["id_user"] for u in body] == ["a1b2c3d4e5"]


def test_router_basics(lam):
    assert call(lam, "OPTIONS", "/usuarios/consultar")[0] == 204
    assert call(lam, "GET", "/otra/ruta")[0] == 404
    assert call(lam, "PATCH", "/usuarios/agregar")[0] == 405


# ---------- Flask with DATA_BACKEND=aws and PRODUCTS_BACKEND=aws ----------
@pytest.fixture
def aws_app(monkeypatch):
    table = FakeTable()
    http = RouterHttp(ProductServiceImplementation(table), UserDynamoImplementation(table),
                      carts=CartDynamoImplementation(table), favorites=FavDynamoImplementation(table))
    monkeypatch.setattr(api_client, "requests", types.SimpleNamespace(
        request=http.request, RequestException=requests.RequestException))
    monkeypatch.setattr(api_client, "API_KEY", KEY)
    client = ProductApiClient(base_url="https://api.example.test", api_key=KEY, http=http)
    from Implementation.DataSource.MemoryStore import MemoryStore
    from Implementation.DataSource.Seed import seed
    store = MemoryStore()
    seed(store, product_count=600)
    upload_users(api_client, build_users(store), out=lambda *a: None)
    upload(client, build_payloads(600, store), out=lambda *a, **k: None)
    quick = QuickApiClient(base_url="https://api.example.test", api_key=KEY, http=http)
    services = build_services(product_count=600, backend="aws", products_backend="aws",
                              product_client=client, users_api=api_client, quick_client=quick)
    return create_app(services, config={"TESTING": True, "SECRET_KEY": "t"}).test_client(), table


def test_sign_in_with_a_user_stored_in_dynamodb(aws_app):
    web, table = aws_app
    assert ("USER#u-ana00001", "PROFILE") in table.items
    html = web.get("/account/access").get_data(as_text=True)
    token = html.split('name="csrf_token" value="')[1].split('"')[0]
    resp = web.post("/account/signin", data={"csrf_token": token, "email": "ana@example.com", "password": "Demo1234"})
    assert resp.status_code == 302
    assert web.get("/cart").status_code == 200


def test_cart_and_favorites_are_saved_in_dynamodb(aws_app):
    web, table = aws_app
    token = web.get("/account/access").get_data(as_text=True).split('name="csrf_token" value="')[1].split('"')[0]
    web.post("/account/signin", data={"csrf_token": token, "email": "ana@example.com", "password": "Demo1234"})
    page = web.get("/product/PRD-000004").get_data(as_text=True)
    token = page.split('name="csrf_token" value="')[1].split('"')[0]
    size = page.split('name="size" value="')[1].split('"')[0]
    color = page.split('name="color" value="')[1].split('"')[0]
    web.post("/cart/add", data={"csrf_token": token, "product_id": "PRD-000004", "size": size,
                                "color": color, "qty": "2"})
    web.post("/favorites/toggle", data={"csrf_token": token, "product_id": "PRD-000004", "next": "/favorites"})
    cart = table.items[("CART#u-ana00001", "CART")]
    assert cart["lines"][0]["product_id"] == "PRD-000004" and int(cart["lines"][0]["qty"]) == 2
    assert "PRD-000004" in table.items[("FAV#u-ana00001", "FAV")]["items"]


def test_register_creates_the_user_in_dynamodb(aws_app):
    web, table = aws_app
    html = web.get("/account/access").get_data(as_text=True)
    token = html.split('name="csrf_token" value="')[1].split('"')[0]
    resp = web.post("/account/register", data={
        "csrf_token": token, "name": "Rosa Pérez", "email": "rosa@moda.mx", "password": "Moda2026",
        "confirm": "Moda2026", "role": "buyer", "terms": "1"})
    assert resp.status_code == 302
    stored = [i for i in table.items.values() if i.get("email") == "rosa@moda.mx"]
    assert stored and stored[0]["password_hash"].startswith(("scrypt:", "pbkdf2:"))
    assert stored[0]["GSI1PK"] == "EMAIL#rosa@moda.mx"
