"""Atomic transactions in DynamoDB (TransactWriteItems run by the Lambda):

1. /productos/reservar  create order -> discount the stock of every product at once
2. /productos/liberar   cancel order -> give every unit back, only once

FakeTable.meta.client.transact_write_items checks EVERY condition before
applying anything, like DynamoDB. Lambda tests run against the layered code
and against the generated lambda_deploy/lambda_function.py."""
import importlib.util
import json
from decimal import Decimal

import pytest

from Implementation.ProductServiceImplementation.ProductServiceImplementation import \
    ProductServiceImplementation
from Interfaces.DataInterfaces.RepositoryErrors import DatabaseUnavailable
from Interfaces.Validators import ValidationError
from scripts.build_single_file_lambda import OUT as SINGLE_FILE
from scripts.load_to_dynamo import build_payloads, upload
from Services.ProductService.ProductService import handle_productos as lambda_handler
from Services.ServiceContainer import build_services
from tests.conftest import ANA, some_product
from tests.fake_dynamo import FakeTable, LambdaHttp
from View.MainViews.Utils.product_api import ProductApiClient

KEY = "tx-key-123"
PAYLOADS = build_payloads(1200)
CHECKOUT = {"street": "Calle 1", "city": "Cuernavaca", "state": "Morelos", "zip_code": "62000", "method": "cash"}


def _single():
    spec = importlib.util.spec_from_file_location("tx_single_file", SINGLE_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SINGLE = _single()


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("MARKETPLACE_API_KEY", KEY)


@pytest.fixture
def table():
    return FakeTable()


@pytest.fixture(params=["layers", "single_file"])
def lam(request, table):
    if request.param == "layers":
        service = ProductServiceImplementation(table)
        service._handler = lambda_handler
    else:
        service = SINGLE.ProductServiceImplementation(table)
        service._handler = SINGLE.handle_productos
    for payload in PAYLOADS[:3]:
        assert call(service, "/productos/agregar", payload)[0] == 201
    return service


def call(service, path, body, key=KEY):
    event = {"rawPath": path, "requestContext": {"http": {"method": "POST"}},
             "headers": {"x-api-key": key} if key else {}, "body": json.dumps(body)}
    out = service._handler(event, service=service)
    return out["statusCode"], json.loads(out["body"])


def stock(table, pid):
    return int(table.items[(f"PRODUCT#{pid}", "METADATA")]["stock"])


def reservation(table, order_id):
    return table.items.get((f"RESERVATION#{order_id}", "STOCK"))


A, B, C = (p["product_id"] for p in PAYLOADS[:3])


# ---------------- Lambda ----------------
def test_reserve_discounts_every_product_in_one_transaction(lam, table):
    before = {p: stock(table, p) for p in (A, B)}
    status, body = call(lam, "/productos/reservar", {"order_id": "10001", "items": [
        {"product_id": A, "qty": 1}, {"product_id": B, "qty": 2}, {"product_id": A, "qty": 1}]})
    assert status == 200 and body["stock"] == {A: before[A] - 2, B: before[B] - 2}   # same product added up
    assert stock(table, A) == before[A] - 2 and stock(table, B) == before[B] - 2
    r = reservation(table, "10001")
    assert r["status"] == "RESERVED" and r["items"] == {A: 2, B: 2} and r["entity"] == "STOCK_RESERVATION"
    assert table.meta.client.transactions == 1


def test_one_product_without_stock_changes_nothing(lam, table):
    before = {p: stock(table, p) for p in (A, B, C)}
    status, body = call(lam, "/productos/reservar", {"order_id": "10001", "items": [
        {"product_id": A, "qty": 1}, {"product_id": B, "qty": before[B] + 1}, {"product_id": C, "qty": 1}]})
    assert status == 409 and body["code"] == "OUT_OF_STOCK" and f"Only {before[B]} left" in body["error"]
    assert {p: stock(table, p) for p in (A, B, C)} == before          # all or nothing
    assert reservation(table, "10001") is None


def test_an_order_number_can_be_reserved_once(lam, table):
    assert call(lam, "/productos/reservar", {"order_id": "10001", "items": [{"product_id": A, "qty": 1}]})[0] == 200
    before = stock(table, A)
    status, body = call(lam, "/productos/reservar", {"order_id": "10001", "items": [{"product_id": A, "qty": 1}]})
    assert status == 409 and body["code"] == "RESERVED" and stock(table, A) == before


def test_release_gives_the_stock_back_only_once(lam, table):
    before = {p: stock(table, p) for p in (A, B)}
    call(lam, "/productos/reservar", {"order_id": "10001", "items": [{"product_id": A, "qty": 3},
                                                                   {"product_id": B, "qty": 1}]})
    status, body = call(lam, "/productos/liberar", {"order_id": "10001"})
    assert status == 200 and body["released"] is True and body["stock"] == before
    assert reservation(table, "10001")["status"] == "RELEASED" and "released_at" in reservation(table, "10001")
    status, body = call(lam, "/productos/liberar", {"order_id": "10001"})       # second admin, same order
    assert status == 200 and body["released"] is False
    assert {p: stock(table, p) for p in (A, B)} == before                     # not restocked twice


def test_release_race_is_decided_by_the_condition(lam, table, monkeypatch):
    """Both admins read RESERVED; the second transaction fails its condition."""
    before = stock(table, A)
    call(lam, "/productos/reservar", {"order_id": "10001", "items": [{"product_id": A, "qty": 2}]})
    original = table.get_item
    stale = dict(original(Key={"PK": "RESERVATION#10001", "SK": "STOCK"})["Item"])
    assert call(lam, "/productos/liberar", {"order_id": "10001"})[1]["released"] is True
    monkeypatch.setattr(table, "get_item", lambda Key, ConsistentRead=False:
                        {"Item": dict(stale)} if Key["PK"].startswith("RESERVATION#") else original(Key=Key))
    status, body = call(lam, "/productos/liberar", {"order_id": "10001"})
    assert status == 200 and body["released"] is False and stock(table, A) == before


def test_release_without_reservation(lam):
    status, body = call(lam, "/productos/liberar", {"order_id": "10099"})
    assert status == 404 and body["code"] == "NO_RESERVATION"


@pytest.mark.parametrize("body", [
    {"order_id": "1 OR 1=1", "items": [{"product_id": "PRD-000001", "qty": 1}]},
    {"order_id": "10001", "items": []},
    {"order_id": "10001", "items": [{"product_id": "PRD-000001", "qty": 0}]},
    {"order_id": "10001", "items": [{"product_id": "PRD-000001", "qty": True}]},
    {"order_id": "10001", "items": [{"product_id": "PRD-000001", "qty": 1001}]},
    {"order_id": "10001", "items": [{"product_id": "PRODUCT#X", "qty": 1}]},
    {"order_id": "10001", "items": [{"product_id": f"PRD-{n:06d}", "qty": 1} for n in range(1, 52)]},
    {"order_id": "10001", "items": "PRD-000001"},
])
def test_bad_reservations_are_rejected(lam, table, body):
    status, _ = call(lam, "/productos/reservar", body)
    assert status == 400 and reservation(table, "10001") is None and table.meta.client.transactions == 0


def test_stock_routes_need_the_api_key(lam, table):
    assert call(lam, "/productos/reservar", {"order_id": "10001", "items": [{"product_id": A, "qty": 1}]},
                key="wrong")[0] == 403
    assert call(lam, "/productos/liberar", {"order_id": "10001"}, key=None)[0] == 403
    assert table.meta.client.transactions == 0


# ---------------- Flask -> Lambda -> DynamoDB ----------------
@pytest.fixture
def aws(table):
    service = ProductServiceImplementation(table)
    service._handler = lambda_handler
    http = LambdaHttp(service)
    client = ProductApiClient(base_url="https://api.example.test", api_key=KEY, http=http)
    upload(client, PAYLOADS, batch=100, out=lambda *a, **k: None)
    services = build_services(product_count=1200, products_backend="aws", product_client=client)
    services.http = http
    return services


def fill_cart(services, n=2, qty=2):
    picked = [p for p in services.store.products.values() if p["stock"] > 5 and p["status"] == "active"][:n]
    for p in picked:
        services.cart.add(ANA, {"product_id": p["product_id"], "size": p["sizes"][0], "color": p["colors"][0],
                                "qty": str(qty)})
    return picked


def test_checkout_uses_one_atomic_transaction(aws, table):
    picked = fill_cart(aws)
    before = {p["product_id"]: stock(table, p["product_id"]) for p in picked}
    aws.http.calls.clear()
    order_id = aws.pay.place_order(ANA, CHECKOUT)
    routes = [path for _, path in aws.http.calls]
    assert routes.count("/productos/reservar") == 1 and "/productos/stock" not in routes
    assert table.meta.client.transactions == 1
    for pid, n in before.items():
        assert stock(table, pid) == n - 2 and aws.store.products[pid]["stock"] == n - 2   # cache updated
    assert reservation(table, order_id)["status"] == "RESERVED"
    assert aws.pay.my_order(ANA, order_id).status == "Pending"


def test_cancel_releases_the_reservation(aws, table):
    picked = fill_cart(aws)
    before = {p["product_id"]: stock(table, p["product_id"]) for p in picked}
    order_id = aws.pay.place_order(ANA, CHECKOUT)
    aws.pay.change_status(order_id, {"new_status": "Cancelled"})
    assert {pid: stock(table, pid) for pid in before} == before
    assert {pid: aws.store.products[pid]["stock"] for pid in before} == before
    assert reservation(table, order_id)["status"] == "RELEASED"
    with pytest.raises(ValidationError):
        aws.pay.change_status(order_id, {"new_status": "Cancelled"})        # never twice
    assert {pid: stock(table, pid) for pid in before} == before


def test_a_number_reserved_by_another_checkout_is_skipped(aws, table):
    fill_cart(aws, n=1)
    taken = aws.store.finance.next_order_id()
    table.items[(f"RESERVATION#{taken}", "STOCK")] = {"PK": f"RESERVATION#{taken}", "SK": "STOCK",
                                                      "status": "RESERVED", "items": {}}
    order_id = aws.pay.place_order(ANA, CHECKOUT)
    assert order_id == taken + 1


def test_failed_order_write_releases_the_stock(aws, table, monkeypatch):
    picked = fill_cart(aws)
    before = {p["product_id"]: stock(table, p["product_id"]) for p in picked}

    def down(*args, **kwargs):
        raise DatabaseUnavailable("MySQL is down")
    monkeypatch.setattr(aws.store.finance, "create_order", down)
    with pytest.raises(DatabaseUnavailable):
        aws.pay.place_order(ANA, CHECKOUT)
    assert {pid: stock(table, pid) for pid in before} == before
    reservations = [v for (pk, _), v in table.items.items() if pk.startswith("RESERVATION#")]
    assert len(reservations) == 1 and reservations[0]["status"] == "RELEASED"
    assert len(aws.store.quick.get_cart(ANA)) == 2                          # the cart is kept


def test_last_unit_race_changes_nothing(aws, table):
    first, second = fill_cart(aws, qty=1)
    first_before = stock(table, first["product_id"])
    table.items[(f"PRODUCT#{second['product_id']}", "METADATA")]["stock"] = Decimal(0)   # another buyer won
    with pytest.raises(ValidationError, match="Only 0 left"):
        aws.pay.place_order(ANA, CHECKOUT)
    assert stock(table, first["product_id"]) == first_before and table.meta.client.transactions == 0
    assert aws.store.products[second["product_id"]]["stock"] == 0           # page shows the real stock


def test_orders_from_before_reservations_still_restock(aws, table):
    """Orders created before this change have no reservation: units return one by one."""
    fill_cart(aws, n=1, qty=3)
    order_id = aws.pay.place_order(ANA, CHECKOUT)
    pid = aws.pay.my_order(ANA, order_id).items[0].product_id
    del table.items[(f"RESERVATION#{order_id}", "STOCK")]
    before = stock(table, pid)
    aws.pay.change_status(order_id, {"new_status": "Cancelled"})
    assert stock(table, pid) == before + 3


def test_memory_mode_follows_the_same_rules(services):
    p = some_product(services)
    services.cart.add(ANA, {"product_id": p["product_id"], "size": p["sizes"][0], "color": p["colors"][0], "qty": "2"})
    before = p["stock"]
    order_id = services.pay.place_order(ANA, CHECKOUT)
    assert services.store.products[p["product_id"]]["stock"] == before - 2
    services.pay.change_status(order_id, {"new_status": "Cancelled"})
    assert services.store.products[p["product_id"]]["stock"] == before
    assert services.store.product_repo.reservations[str(order_id)]["status"] == "RELEASED"
