"""MySQL (finance_db) and PostgreSQL repositories, tested on SQLite copies of
the real scripts. Contract tests run against the memory AND the SQL version,
so both behave the same; the end-to-end tests run the Flask app with orders,
reviews, sales, alerts, categories and sellers stored in the databases."""
import re
from datetime import date, timedelta
from decimal import Decimal

import pytest

from Implementation.DataSource.MemoryRepositories import (MemoryFinanceRepository,
                                                          MemoryOperationsRepository)
from Implementation.DataSource.MemoryStore import MemoryStore
from Implementation.DataSource.MySQLFinanceRepository import MySQLFinanceRepository
from Implementation.DataSource.PostgresOperationsRepository import PostgresOperationsRepository
from Implementation.DataSource.Seed import seed
from Interfaces.DataInterfaces.RepositoryErrors import (DatabaseUnavailable, DuplicateKey,
                                                        StaleWrite)
from Interfaces.Validators import ValidationError
from scripts.load_to_sql import build_rows, load
from Services.ServiceContainer import build_services
from tests.sql_schemas import FINANCE, OPERATIONS, raw_rows, sqlite_database
from View.MainViews.PY.app import create_app

COUNT = 400
ROWS = build_rows(COUNT)
NOW = "2026-11-20T10:00:00"


def order_rows(oid, buyer="u-ana00001", status="Pending"):
    order = {"order_id": oid, "buyer_id": buyer, "created_at": NOW, "status": status,
             "subtotal": Decimal("862.07"), "tax_amount": Decimal("137.93"), "total_amount": Decimal("1000.00")}
    items = [{"product_id": "PRD-000001", "quantity": 2, "unit_price": Decimal("500.00"), "size": "M", "color": "Red"}]
    shipping = {"street": "Calle 1", "city": "Cuernavaca", "state": "Morelos", "zip_code": "62000"}
    payment = {"payment_id": f"PAY-{oid}", "payment_method": "spei", "card_last4": "",
               "status": "Awaiting payment", "processed_at": NOW}
    history = {"old_status": None, "new_status": "Pending", "changed_at": NOW}
    return order, items, shipping, payment, history


# ---------------- fixtures ----------------
@pytest.fixture
def finance_db(tmp_path):
    return sqlite_database(str(tmp_path / "finance.db"), FINANCE, "MySQL finance_db (test)")


@pytest.fixture
def ops_db(tmp_path):
    db = sqlite_database(str(tmp_path / "operations.db"), OPERATIONS, "PostgreSQL (test)")
    load(db, ROWS, out=lambda *a: None)
    return db


@pytest.fixture(params=["memory", "mysql"])
def finance(request, finance_db):
    return MemoryFinanceRepository(MemoryStore()) if request.param == "memory" else MySQLFinanceRepository(finance_db)


@pytest.fixture(params=["memory", "postgres"])
def ops(request, ops_db):
    if request.param == "memory":
        store = MemoryStore()
        seed(store, product_count=COUNT)
        return MemoryOperationsRepository(store)
    return PostgresOperationsRepository(ops_db)


# ---------------- finance contract (memory and MySQL) ----------------
def test_order_round_trip(finance):
    oid = finance.next_order_id()
    assert oid >= 10001
    finance.create_order(*order_rows(oid))
    full = finance.get_order(oid)
    assert full["order"]["status"] == "Pending" and full["order"]["total_amount"] == Decimal("1000.00")
    assert full["items"][0]["size"] == "M" and full["items"][0]["unit_price"] == Decimal("500.00")
    assert full["payment"]["payment_method"] == "spei" and full["payment"]["status"] == "Awaiting payment"
    assert full["shipping"]["zip_code"] == "62000"
    assert [h["new_status"] for h in full["history"]] == ["Pending"]
    assert finance.next_order_id() == oid + 1


def test_duplicate_order_id(finance):
    finance.create_order(*order_rows(10001))
    with pytest.raises(DuplicateKey):
        finance.create_order(*order_rows(10001))


def test_status_change_is_atomic_and_checked(finance):
    finance.create_order(*order_rows(10001))
    invoice = {"invoice_id": 20001, "pdf_receipt_url": "/orders/10001/invoice", "issued_at": NOW}
    finance.update_status(10001, "Pending", "Paid", NOW, "Approved", invoice)
    full = finance.get_order(10001)
    assert full["order"]["status"] == "Paid" and full["payment"]["status"] == "Approved"
    assert full["invoice"]["invoice_id"] == 20001
    assert [(h["old_status"], h["new_status"]) for h in full["history"]] == [(None, "Pending"), ("Pending", "Paid")]
    with pytest.raises(StaleWrite):           # someone else already moved it
        finance.update_status(10001, "Pending", "Cancelled", NOW, "Cancelled")
    assert finance.get_order(10001)["order"]["status"] == "Paid"


def test_list_filters_and_has_bought(finance):
    finance.create_order(*order_rows(10001))
    finance.create_order(*order_rows(10002, buyer="u-buyer0001"))
    finance.update_status(10002, "Pending", "Cancelled", NOW, "Cancelled")
    assert [o["order"]["order_id"] for o in finance.list_orders(buyer_id="u-ana00001")] == [10001]
    assert [o["order"]["order_id"] for o in finance.list_orders(status="Cancelled")] == [10002]
    assert [o["order"]["order_id"] for o in finance.list_orders(order_id_contains="002")] == [10002]
    assert finance.has_bought("u-ana00001", "PRD-000001")
    assert not finance.has_bought("u-buyer0001", "PRD-000001")     # cancelled orders do not count


def test_mysql_keeps_the_scripts_conventions(finance_db):
    repo = MySQLFinanceRepository(finance_db)
    repo.create_order(*order_rows(10001))
    repo.update_status(10001, "Pending", "Paid", NOW, "Approved",
                       {"invoice_id": 20001, "pdf_receipt_url": "/orders/10001/invoice", "issued_at": NOW})
    assert raw_rows(finance_db, "SELECT status FROM orders")[0]["status"] == "PAID"
    pay = raw_rows(finance_db, "SELECT payment_method, status FROM payments")[0]
    assert pay == {"payment_method": "TRANSFER", "status": "SUCCESS"}
    assert [r["new_status"] for r in raw_rows(finance_db, "SELECT new_status FROM order_status_history "
                                                          "ORDER BY history_id")] == ["PENDING", "PAID"]


def test_sql_injection_is_just_text(finance_db):
    repo = MySQLFinanceRepository(finance_db)
    repo.create_order(*order_rows(10001))
    assert repo.list_orders(buyer_id="x' OR '1'='1") == []
    assert len(raw_rows(finance_db, "SELECT * FROM orders")) == 1


# ---------------- operations contract (memory and PostgreSQL) ----------------
def test_categories(ops):
    cats = ops.list_categories()
    assert len(cats) == len(ROWS["categories"]) and any(c["slug"] == "women-dresses" for c in cats)
    cid = ops.create_category({"parent_id": 1, "name": "Swimwear", "slug": "women-swimwear", "is_active": True})
    assert cid > max(c["category_id"] for c in cats)
    with pytest.raises(DuplicateKey):
        ops.create_category({"parent_id": 1, "name": "Again", "slug": "women-swimwear", "is_active": True})
    ops.update_category(cid, {"parent_id": 1, "name": "Swim", "slug": "women-swim", "is_active": False})
    row = next(c for c in ops.list_categories() if c["category_id"] == cid)
    assert row["name"] == "Swim" and row["is_active"] is False


def test_sellers(ops):
    sid = ops.create_seller({"user_id": "u-new0001", "store_name": "Rosa Moda", "tax_id": "PERR850101AB1",
                             "rating": 0, "status": "active", "created_at": NOW})
    assert re.fullmatch(r"SEL-\d{4}", sid)
    ops.set_seller_status(sid, "suspended")
    assert next(x for x in ops.list_sellers() if x["seller_id"] == sid)["status"] == "suspended"


def test_reviews(ops):
    stats = ops.rating_stats()
    pid = next(iter(stats))
    counts = ops.rating_counts(pid)
    assert sum(counts.values()) == stats[pid][1]
    rid = ops.add_review({"product_id": pid, "buyer_id": "u-ana00001", "rating": 5,
                          "comment": "Great fit", "created_at": "2099-01-01T00:00:00"})
    assert rid and ops.has_reviewed("u-ana00001", pid)
    assert ops.reviews_for(pid, 1)[0]["comment"] == "Great fit"
    with pytest.raises(DuplicateKey):
        ops.add_review({"product_id": pid, "buyer_id": "u-ana00001", "rating": 1,
                        "comment": "Again", "created_at": NOW})


def test_sales_and_alerts(ops):
    today = date.today()
    ops.record_sales([{"product_id": "PRD-000002", "category_id": 12, "seller_id": "SEL-0001",
                       "quantity_sold": 7, "sale_date": today.isoformat()}])
    days = ops.units_by_day("SEL-0001", today - timedelta(days=29))
    assert days[today.isoformat()] >= 7
    assert ops.top_products("SEL-0001", today - timedelta(days=29), 3)
    before = ops.count_open_alerts("SEL-0001")
    aid = ops.insert_alert({"product_id": "PRD-000002", "seller_id": "SEL-0001", "current_stock": 1,
                            "threshold_limit": 5, "alert_status": "Open", "updated_at": NOW})
    assert ops.open_alert("PRD-000002")["alert_id"] == aid
    assert ops.count_open_alerts("SEL-0001") == before + 1
    ops.update_alert(aid, {"alert_status": "Resolved", "current_stock": 30, "drop_table": "x"})
    assert ops.open_alert("PRD-000002") is None          # unknown keys like "drop_table" are ignored
    ops.insert_alert({"product_id": "PRD-000003", "seller_id": "SEL-0001", "current_stock": 0,
                      "threshold_limit": 5, "alert_status": "Open", "updated_at": NOW})
    listed = ops.alerts_for_seller("SEL-0001", 50)
    assert listed[0]["alert_status"] == "Open"           # open alerts first
    assert any(a["alert_id"] == aid and a["alert_status"] == "Resolved" for a in listed)


def test_load_script_refuses_to_mix_data(ops_db):
    with pytest.raises(SystemExit, match="--replace"):
        load(ops_db, ROWS, out=lambda *a: None)
    load(ops_db, ROWS, replace=True, out=lambda *a: None)
    assert len(raw_rows(ops_db, "SELECT * FROM reviews")) == len(ROWS["reviews"])


# ---------------- the app on the databases ----------------
def app_on(finance_db, ops_db):
    services = build_services(product_count=COUNT, finance_backend="mysql", finance_db=finance_db,
                              operations_backend="postgres", operations_db=ops_db)
    return services, create_app(services, config={"TESTING": True, "SECRET_KEY": "t"}).test_client()


def token(web, url="/account/access"):
    return re.search(r'name="csrf_token" value="([0-9a-f]+)"', web.get(url).get_data(as_text=True)).group(1)


def sign_in(web, email):
    assert web.post("/account/signin", data={"csrf_token": token(web), "email": email,
                                             "password": "Demo1234"}).status_code == 302


def buy(services, web, product, method="card"):
    tok = token(web, f"/product/{product['product_id']}")
    web.post("/cart/add", data={"csrf_token": tok, "product_id": product["product_id"],
                                "size": product["sizes"][0], "color": product["colors"][0], "qty": "1"})
    data = {"csrf_token": tok, "street": "Av. Morelos 5", "city": "Cuernavaca", "state": "Morelos",
            "zip_code": "62000", "method": method}
    if method == "card":
        data.update(card_number="4242424242424242", expiry="12/30", cvv="123")
    resp = web.post("/checkout", data=data)
    assert resp.status_code == 302 and "/confirmation" in resp.location, resp.get_data(as_text=True)[:300]
    return int(resp.location.split("/orders/")[1].split("/")[0])


def test_app_reads_and_writes_the_databases(finance_db, ops_db):
    services, web = app_on(finance_db, ops_db)
    assert not services.store.orders                      # no demo orders: the tables decide
    sign_in(web, "ana@example.com")
    product = next(p for p in services.store.products.values()
                   if p["status"] == "active" and p["stock"] > 3 and p["seller_id"] == "SEL-0001")
    sales_before = sum(r["quantity_sold"] for r in raw_rows(ops_db, "SELECT quantity_sold FROM analytics"))
    order_id = buy(services, web, product)

    # MySQL has the whole order
    order = raw_rows(finance_db, f"SELECT * FROM orders WHERE order_id = '{order_id}'")[0]
    assert order["status"] == "PAID" and order["buyer_id"] == "u-ana00001"
    assert raw_rows(finance_db, "SELECT payment_method, status, card_last4 FROM payments")[0] == \
        {"payment_method": "CARD", "status": "SUCCESS", "card_last4": "4242"}
    assert len(raw_rows(finance_db, "SELECT * FROM invoices")) == 1
    assert "4242424242424242" not in str(raw_rows(finance_db, "SELECT * FROM payments"))
    # PostgreSQL got the sale
    assert sum(r["quantity_sold"] for r in raw_rows(ops_db, "SELECT quantity_sold FROM analytics")) == sales_before + 1

    # the screens read from the databases
    assert f"ORD-{order_id}" in web.get("/orders").get_data(as_text=True)
    assert web.get(f"/orders/{order_id}/invoice").status_code == 200

    # review: allowed only after buying, stored in PostgreSQL, shown on the product page
    tok = token(web, f"/product/{product['product_id']}")
    web.post(f"/product/{product['product_id']}/reviews", data={"csrf_token": tok, "rating": "5",
                                                                "comment": "Saved in PostgreSQL"})
    assert raw_rows(ops_db, "SELECT comment FROM reviews WHERE buyer_id = 'u-ana00001'")[0]["comment"] == \
        "Saved in PostgreSQL"
    assert "Saved in PostgreSQL" in web.get(f"/product/{product['product_id']}").get_data(as_text=True)


def test_data_survives_a_restart(finance_db, ops_db):
    services, web = app_on(finance_db, ops_db)
    sign_in(web, "ana@example.com")
    product = next(p for p in services.store.products.values() if p["status"] == "active" and p["stock"] > 3)
    order_id = buy(services, web, product, method="cash")
    _, web2 = app_on(finance_db, ops_db)                 # a brand-new app process
    sign_in(web2, "ana@example.com")
    assert f"ORD-{order_id}" in web2.get("/orders").get_data(as_text=True)


def test_admin_changes_go_to_the_databases(finance_db, ops_db):
    services, web = app_on(finance_db, ops_db)
    sign_in(web, "ana@example.com")
    product = next(p for p in services.store.products.values() if p["status"] == "active" and p["stock"] > 3)
    order_id = buy(services, web, product, method="cash")
    web.post("/account/signout", data={"csrf_token": token(web, "/cart")})
    sign_in(web, "admin@marketplace.mx")
    tok = token(web, "/admin/orders")
    web.post(f"/admin/orders/{order_id}/status", data={"csrf_token": tok, "new_status": "Paid"})
    web.post(f"/admin/orders/{order_id}/status", data={"csrf_token": tok, "new_status": "Shipped"})
    history = raw_rows(finance_db, "SELECT new_status FROM order_status_history ORDER BY history_id")
    assert [h["new_status"] for h in history] == ["PENDING", "PAID", "SHIPPED"]

    web.post("/admin/categories", data={"csrf_token": tok, "name": "Swimwear", "slug": "women-swimwear",
                                        "parent_id": "1", "is_active": "1"})
    row = raw_rows(ops_db, "SELECT category_id, is_active FROM categories WHERE slug = 'women-swimwear'")[0]
    web.post(f"/admin/categories/{row['category_id']}/toggle", data={"csrf_token": tok})
    assert not raw_rows(ops_db, "SELECT is_active FROM categories WHERE slug = 'women-swimwear'")[0]["is_active"]


def test_seller_dashboard_and_registration(finance_db, ops_db):
    services, web = app_on(finance_db, ops_db)
    sign_in(web, "carlos@urbanthread.mx")
    html = web.get("/seller").get_data(as_text=True)
    assert "Hello, Urban Thread" in html and "Units sold" in html
    web.post("/account/signout", data={"csrf_token": token(web, "/seller")})
    resp = web.post("/account/register", data={
        "csrf_token": token(web), "name": "Rosa Pérez", "email": "rosa@moda.mx", "password": "Moda2026",
        "confirm": "Moda2026", "role": "seller", "store_name": "Rosa Moda", "tax_id": "PERR850101AB1", "terms": "1"})
    assert resp.status_code == 302
    assert raw_rows(ops_db, "SELECT status FROM sellers WHERE store_name = 'Rosa Moda'")[0]["status"] == "ACTIVE"
    assert "Hello, Rosa Moda" in web.get("/seller").get_data(as_text=True)


def test_database_down_shows_an_error_and_returns_the_stock(finance_db, ops_db):
    services, web = app_on(finance_db, ops_db)
    sign_in(web, "ana@example.com")
    product = next(p for p in services.store.products.values() if p["status"] == "active" and p["stock"] > 3)
    stock = product["stock"]

    def broken():
        raise OSError("connection refused")
    services.store.finance.db._connect = broken
    tok = token(web, f"/product/{product['product_id']}")
    web.post("/cart/add", data={"csrf_token": tok, "product_id": product["product_id"],
                                "size": product["sizes"][0], "color": product["colors"][0], "qty": "2"})
    resp = web.post("/checkout", data={"csrf_token": tok, "street": "X 1", "city": "Y", "state": "Morelos",
                                       "zip_code": "62000", "method": "cash"})
    assert resp.status_code == 200 and "Could not connect to the database" in resp.get_data(as_text=True)
    assert services.store.products[product["product_id"]]["stock"] == stock      # stock given back
    assert web.get(f"/product/{product['product_id']}").status_code == 200   # still browsable
    resp = web.get("/orders")
    assert resp.status_code == 503 and "right back" in resp.get_data(as_text=True)


def test_postgres_without_data_gives_a_clear_error(tmp_path):
    empty = sqlite_database(str(tmp_path / "empty.db"), OPERATIONS, "PostgreSQL (empty)")
    with pytest.raises(RuntimeError, match="load_to_sql"):
        build_services(product_count=50, operations_backend="postgres", operations_db=empty)


def test_validation_errors_are_friendly():
    assert issubclass(DatabaseUnavailable, ValidationError) and issubclass(StaleWrite, ValidationError)
