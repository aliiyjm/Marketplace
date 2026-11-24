"""Products in DynamoDB: the Lambda (routes, auth, validation, conditional
stock) and the Flask app running with PRODUCTS_BACKEND=aws end to end:
Flask -> ProductApiClient -> API Gateway event -> lambda_handler -> table."""
import importlib.util
import json
import subprocess
import sys
from decimal import Decimal

import pytest

from Implementation.ProductServiceImplementation.ProductServiceImplementation import \
    ProductServiceImplementation
from Interfaces.Validators import ValidationError
from scripts.build_single_file_lambda import OUT as SINGLE_FILE
from scripts.build_single_file_lambda import generate
from scripts.load_to_dynamo import build_payloads, upload
from Services.ProductService.ProductService import handle_productos as lambda_handler
from Services.ServiceContainer import build_services
from tests.conftest import ANA, CARLOS_SELLER
from tests.fake_dynamo import FakeTable, LambdaHttp
from View.MainViews.Utils.product_api import ProductApiClient

KEY = "test-key-123"
PAYLOADS = build_payloads(1200)


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("MARKETPLACE_API_KEY", KEY)


def _single_file_module():
    spec = importlib.util.spec_from_file_location("productos_single_file", SINGLE_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SINGLE = _single_file_module()


@pytest.fixture
def table():
    return FakeTable()


@pytest.fixture(params=["layers", "single_file"])
def impl(request, table):
    """Every Lambda test runs twice: against the layered code and against the
    generated lambda_deploy/lambda_function.py that gets pasted in AWS."""
    if request.param == "layers":
        service = ProductServiceImplementation(table)
        service._handler = lambda_handler
    else:
        service = SINGLE.ProductServiceImplementation(table)
        service._handler = SINGLE.handle_productos
    return service


@pytest.fixture
def client(impl):
    return ProductApiClient(base_url="https://api.example.test", api_key=KEY, http=LambdaHttp(impl))


@pytest.fixture
def loaded(client):
    upload(client, PAYLOADS, batch=100, out=lambda *a, **k: None)
    return client


@pytest.fixture
def services(loaded):
    return build_services(product_count=1200, products_backend="aws", product_client=loaded)


def event(method, path, *, query=None, body=None, key=KEY, v1=False):
    headers = {"X-Api-Key": key} if key else {}
    if v1:
        return {"httpMethod": method, "path": path, "headers": headers,
                "queryStringParameters": query, "body": json.dumps(body) if body is not None else None}
    return {"rawPath": path, "requestContext": {"http": {"method": method}}, "headers": headers,
            "queryStringParameters": query, "body": json.dumps(body) if body is not None else None}


def call(impl, *args, **kwargs):
    handler = getattr(impl, "_handler", lambda_handler)
    out = handler(event(*args, **kwargs), service=impl)
    return out["statusCode"], json.loads(out["body"])


def item_in(table, product_id):
    return table.items[(f"PRODUCT#{product_id}", "METADATA")]


# ---------------- Lambda ----------------
def test_writes_need_the_api_key(impl, monkeypatch):
    assert call(impl, "POST", "/productos/agregar", body=PAYLOADS[0], key="wrong")[0] == 403
    assert call(impl, "POST", "/productos/agregar", body=PAYLOADS[0], key=None)[0] == 403
    monkeypatch.delenv("MARKETPLACE_API_KEY")
    assert call(impl, "POST", "/productos/agregar", body=PAYLOADS[0])[0] == 503   # fail closed
    assert call(impl, "GET", "/productos/consultar", query={"limit": "5"}, key=None)[0] == 200


def test_create_get_and_duplicate(impl, table):
    status, body = call(impl, "POST", "/productos/agregar", body=PAYLOADS[0])
    assert status == 201 and body["product_id"] == "PRD-000001"
    stored = item_in(table, "PRD-000001")
    assert stored["GSI1PK"].startswith("CATEGORY#") and stored["GSI1SK"].startswith("PRICE#")
    assert isinstance(stored["price"], Decimal)
    status, body = call(impl, "GET", "/productos/consultar", query={"product_id": "PRD-000001"}, key=None)
    assert status == 200 and body["price"] == PAYLOADS[0]["price"] and body["sizes"] == PAYLOADS[0]["sizes"]
    assert call(impl, "POST", "/productos/agregar", body=PAYLOADS[0])[0] == 409
    assert call(impl, "GET", "/productos/consultar", query={"product_id": "PRD-999999"})[0] == 404


@pytest.mark.parametrize("change", [
    {"price": "-1"}, {"product_id": "X1"}, {"stock": 2.5}, {"sizes": []}, {"status": "hacked"},
    {"attributes": {"<script>": "x"}}, {"seller_id": "u-ana00001"}, {"kind": "spaceship"},
])
def test_invalid_products_are_rejected(impl, table, change):
    status, body = call(impl, "POST", "/productos/agregar", body={**PAYLOADS[0], **change})
    assert status == 400 and body["error"]
    assert not table.items


def test_bulk_load_and_pagination(impl, client):
    upload(client, PAYLOADS[:250], batch=100, out=lambda *a, **k: None)
    seen, cursor, pages = [], None, 0
    while True:
        items, cursor = client.list_page(cursor, limit=100)
        seen += [i["product_id"] for i in items]
        pages += 1
        if not cursor:
            break
    assert len(seen) == len(set(seen)) == 250 and pages == 3


def test_category_query_uses_the_index_sorted_by_price(loaded):
    items, _ = loaded.list_page(limit=2000, category="women-dresses")
    assert items and all(i["category_slug"] == "women-dresses" for i in items)
    prices = [Decimal(i["price"]) for i in items]
    assert prices == sorted(prices)


def test_stock_never_goes_below_zero(impl, table):
    call(impl, "POST", "/productos/agregar", body={**PAYLOADS[0], "stock": 3})
    status, body = call(impl, "POST", "/productos/stock", body={"product_id": "PRD-000001", "delta": -2})
    assert status == 200 and body["stock"] == 1
    status, body = call(impl, "POST", "/productos/stock", body={"product_id": "PRD-000001", "delta": -2})
    assert status == 409 and body["code"] == "OUT_OF_STOCK"
    assert item_in(table, "PRD-000001")["stock"] == 1
    assert call(impl, "POST", "/productos/stock", body={"product_id": "PRD-000002", "delta": -1})[0] == 404
    assert call(impl, "POST", "/productos/stock", body={"product_id": "PRD-000001", "delta": "5"})[0] == 400


def test_public_reads_hide_drafts_and_deleted(impl):
    call(impl, "POST", "/productos/cargar", body={"items": [PAYLOADS[0], {**PAYLOADS[1], "status": "draft"}]})
    status, body = call(impl, "GET", "/productos/consultar", query={"limit": "10"}, key=None)
    assert [i["product_id"] for i in body["items"]] == ["PRD-000001"]
    assert call(impl, "GET", "/productos/consultar", query={"product_id": "PRD-000002"}, key=None)[0] == 404
    status, body = call(impl, "GET", "/productos/consultar", query={"limit": "10"})      # with the key
    assert len(body["items"]) == 2


def test_soft_delete_keeps_the_item(impl, table):
    call(impl, "POST", "/productos/agregar", body=PAYLOADS[0])
    assert call(impl, "DELETE", "/productos/eliminar", query={"product_id": "PRD-000001"})[0] == 200
    assert item_in(table, "PRD-000001")["status"] == "deleted"
    assert call(impl, "DELETE", "/productos/eliminar", query={"product_id": "PRD-000077"})[0] == 404


def test_event_formats_routes_and_bad_input(impl):
    out = impl._handler(event("GET", "/prod/productos/consultar", query={"limit": "3"}, v1=True), service=impl)
    assert out["statusCode"] == 200                       # REST API (v1) with a stage prefix
    assert call(impl, "GET", "/productos/otra")[0] == 404
    assert call(impl, "PATCH", "/productos/agregar")[0] == 405
    assert call(impl, "GET", "/productos/consultar", query={"cursor": "not-base64!"})[0] == 400
    assert call(impl, "GET", "/productos/consultar", query={"limit": "999999"})[0] == 400
    out = impl._handler({**event("POST", "/productos/agregar"), "body": "{not json"}, service=impl)
    assert out["statusCode"] == 400
    assert call(impl, "POST", "/productos/cargar", body={"items": PAYLOADS[:101]})[0] == 400


def test_internal_errors_do_not_leak(monkeypatch):
    class Broken:
        def get(self, product_id):
            raise RuntimeError("password=secret host=db.internal")
    status, body = call(Broken(), "GET", "/productos/consultar", query={"product_id": "PRD-000001"})
    assert status == 500 and body == {"error": "Internal error."}


def test_single_file_lambda_is_up_to_date():
    assert SINGLE_FILE.read_text(encoding="utf-8") == generate(), \
        "Run: python scripts/build_single_file_lambda.py"


def test_single_file_runs_alone(tmp_path):
    code = (f"import importlib.util as u; s=u.spec_from_file_location('lambda_function', {str(SINGLE_FILE)!r}); "
            "m=u.module_from_spec(s); s.loader.exec_module(m); "
            "print(m.lambda_handler({'rawPath': '/productos/x', 'requestContext': {'http': {'method': 'GET'}}})['statusCode'])")
    result = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, cwd=tmp_path)
    assert result.stdout.strip() == "404", result.stderr


# ---------------- Flask app with PRODUCTS_BACKEND=aws ----------------
def test_catalog_comes_from_dynamodb(table, loaded):
    item_in(table, "PRD-000010")["name"] = "Changed In DynamoDB"
    item_in(table, "PRD-000010")["price"] = Decimal("123.00")
    svc = build_services(product_count=1200, products_backend="aws", product_client=loaded)
    assert len(svc.store.products) == 1200
    p = svc.store.products["PRD-000010"]
    assert p["name"] == "Changed In DynamoDB" and p["price"] == Decimal("123.00")
    assert svc.main.home({"q": "changed in dynamodb"}).total == 1
    assert not svc.store.orders          # demo orders are skipped: they would spend real stock


def test_screens_render_with_dynamodb_backend(services):
    from View.MainViews.PY.app import create_app
    web = create_app(services, config={"TESTING": True, "SECRET_KEY": "t"}).test_client()
    assert web.get("/").status_code == 200
    assert web.get("/?cat=shoes&sort=price_asc").status_code == 200
    assert web.get("/product/PRD-000004").status_code == 200


def test_empty_table_gives_a_clear_error(client):
    with pytest.raises(RuntimeError, match="load_to_dynamo"):
        build_services(product_count=50, products_backend="aws", product_client=client)


def test_checkout_discounts_stock_in_dynamodb(services, table):
    p = next(x for x in services.store.products.values() if x["stock"] > 5 and x["status"] == "active")
    before = int(item_in(table, p["product_id"])["stock"])
    services.cart.add(ANA, {"product_id": p["product_id"], "size": p["sizes"][0], "color": p["colors"][0], "qty": "2"})
    order_id = services.pay.place_order(ANA, {"street": "Calle 1", "city": "Cuernavaca", "state": "Morelos",
                                              "zip_code": "62000", "method": "cash"})
    assert int(item_in(table, p["product_id"])["stock"]) == before - 2 == p["stock"]
    services.pay.change_status(order_id, {"new_status": "Cancelled"})
    assert int(item_in(table, p["product_id"])["stock"]) == before == p["stock"]


def test_race_for_the_last_unit_rolls_back(services, table):
    products = [x for x in services.store.products.values() if x["stock"] > 5 and x["status"] == "active"][:2]
    first, second = products
    for p in products:
        services.cart.add(ANA, {"product_id": p["product_id"], "size": p["sizes"][0], "color": p["colors"][0], "qty": "1"})
    first_before = int(item_in(table, first["product_id"])["stock"])
    item_in(table, second["product_id"])["stock"] = Decimal(0)    # another buyer took everything
    with pytest.raises(ValidationError, match="Only 0 left"):
        services.pay.place_order(ANA, {"street": "Calle 1", "city": "Cuernavaca", "state": "Morelos",
                                       "zip_code": "62000", "method": "cash"})
    assert int(item_in(table, first["product_id"])["stock"]) == first_before    # compensated
    assert not services.store.orders and second["stock"] == 0
    assert services.cart_count.for_user(ANA) == 2                              # cart untouched


def test_seller_writes_go_to_dynamodb(services, table):
    class Form(dict):
        def getlist(self, key):
            v = self.get(key, [])
            return v if isinstance(v, list) else [v]
    form = Form(name="Linen Guayabera", description="Fresh", category_id="22", price="899.00", stock="7",
                threshold_limit="2", sizes="S, M, L", colors="White, Beige", visible="1",
                attr_key=["Material"], attr_value=["Linen"])
    pid = services.seller.create(CARLOS_SELLER, form)
    stored = item_in(table, pid)
    assert stored["name"] == "Linen Guayabera" and stored["seller_id"] == CARLOS_SELLER
    assert stored["attributes"] == {"Material": "Linen"} and stored["GSI1PK"] == "CATEGORY#men-shirts"
    services.seller.update(CARLOS_SELLER, pid, Form(form, price="799.00"))
    assert item_in(table, pid)["price"] == Decimal("799.00")
    services.seller.delete(CARLOS_SELLER, pid)
    assert item_in(table, pid)["status"] == "deleted"


def test_product_page_reads_fresh_stock(services, table):
    p = next(x for x in services.store.products.values() if x["status"] == "active" and x["stock"] > 0)
    item_in(table, p["product_id"])["stock"] = Decimal(1)
    page = services.main.product_page(p["product_id"])
    assert page.product.stock == 1
    item_in(table, p["product_id"])["status"] = "deleted"
    assert services.main.product_page(p["product_id"]) is None


def test_database_down_shows_a_friendly_error(services):
    class Down:
        def request(self, *a, **k):
            import requests
            raise requests.ConnectionError("boom")
    services.store.product_repo.client.http = Down()
    p = next(x for x in services.store.products.values() if x["stock"] > 3 and x["status"] == "active")
    services.cart.add(ANA, {"product_id": p["product_id"], "size": p["sizes"][0], "color": p["colors"][0], "qty": "1"})
    with pytest.raises(ValidationError, match="Could not"):
        services.pay.place_order(ANA, {"street": "Calle 1", "city": "Cuernavaca", "state": "Morelos",
                                       "zip_code": "62000", "method": "cash"})
    assert services.main.product_page(p["product_id"]) is not None          # falls back to the cache
