"""Stored procedures (MySQL finance_db) and stored functions (PostgreSQL).

SQLite cannot run procedures, so these tests check:
- the CONTRACT (cancel_order, buyer_history, seller_history) on memory and SQL,
- that with MySQL/PostgreSQL the app really CALLs the routines, with every value
  as a parameter, and translates their SIGNAL codes,
- the scripts that install them and test them on a temporary database.
The procedures themselves run on the real servers with:
    python scripts/verify_procedures_live.py
"""
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from Implementation.DataSource.MemoryRepositories import (MemoryFinanceRepository,
                                                          MemoryOperationsRepository)
from Implementation.DataSource.MemoryStore import MemoryStore
from Implementation.DataSource.MySQLFinanceRepository import MySQLFinanceRepository
from Implementation.DataSource.PostgresOperationsRepository import PostgresOperationsRepository
from Implementation.DataSource.Seed import seed
from Implementation.DataSource.SqlConnection import explain, procedure_code
from Interfaces.DataInterfaces.RepositoryErrors import ProcedureError, StaleWrite
from Interfaces.Validators import ValidationError
from scripts.install_procedures import (MYSQL_ROUTINES, POSTGRES_FILE, POSTGRES_ROUTINES,
                                        mysql_statements, split_mysql_script)
from scripts.load_to_sql import build_rows, load
from scripts.verify_procedures_live import finance_ddl
from tests.conftest import login
from tests.sql_schemas import FINANCE, OPERATIONS, sqlite_database
from tests.test_sql_databases import NOW, order_rows

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(params=["memory", "mysql"])
def finance(request, tmp_path):
    if request.param == "memory":
        return MemoryFinanceRepository(MemoryStore())
    return MySQLFinanceRepository(sqlite_database(str(tmp_path / "f.db"), FINANCE, "MySQL (test)"))


@pytest.fixture(params=["memory", "postgres"])
def ops(request, tmp_path):
    if request.param == "memory":
        return MemoryOperationsRepository(MemoryStore())
    db = sqlite_database(str(tmp_path / "o.db"), OPERATIONS, "PostgreSQL (test)")
    load(db, build_rows(50), out=lambda *a: None)
    with db.transaction() as cur:
        cur.execute("DELETE FROM analytics")
    return PostgresOperationsRepository(db)


# ---------------- contract: memory and SQL behave the same ----------------
def test_cancel_returns_the_units_and_refunds(finance):
    order, items, shipping, payment, history = order_rows(10001)
    items = items + [dict(items[0], size="L", quantity=1), dict(items[0], product_id="PRD-000009", quantity=3)]
    finance.create_order(order, items, shipping, payment, history)
    finance.update_status(10001, "Pending", "Paid", NOW, "Approved")
    back = finance.cancel_order(10001, "Paid", NOW)
    assert back == {"PRD-000001": 3, "PRD-000009": 3}
    full = finance.get_order(10001)
    assert full["order"]["status"] == "Cancelled" and full["payment"]["status"] == "Refunded"
    assert [h["new_status"] for h in full["history"]] == ["Pending", "Paid", "Cancelled"]


def test_cancel_checks_the_status(finance):
    finance.create_order(*order_rows(10001))
    with pytest.raises(StaleWrite):
        finance.cancel_order(10001, "Paid", NOW)               # someone else changed it
    finance.cancel_order(10001, "Pending", NOW)
    assert finance.get_order(10001)["payment"]["status"] == "Cancelled"
    with pytest.raises(ValidationError):
        finance.cancel_order(10001, "Cancelled", NOW)          # only once


def test_buyer_history_is_consolidated(finance):
    for oid in (10001, 10002, 10003):
        finance.create_order(*order_rows(oid))
    finance.create_order(*order_rows(10004, buyer="u-other001"))
    finance.cancel_order(10002, "Pending", NOW)
    h = finance.buyer_history("u-ana00001")
    assert h["orders_count"] == 3 and h["active_orders"] == 2
    assert h["total_spent"] == Decimal("2000.00") and h["units_bought"] == 4
    assert {o["order_id"] for o in h["orders"]} == {10001, 10002, 10003}
    assert {o["status"] for o in h["orders"]} == {"Pending", "Cancelled"}
    assert all(o["line_count"] == 1 and o["units"] == 2 for o in h["orders"])
    assert finance.buyer_history("nobody")["orders_count"] == 0


def test_seller_history_is_consolidated(ops):
    today = date(2026, 11, 26)
    rows = [("PRD-000001", 2, today), ("PRD-000001", 1, today - timedelta(days=2)),
            ("PRD-000002", 5, today), ("PRD-000003", 9, today - timedelta(days=60))]
    ops.record_sales([{"product_id": pid, "category_id": 1, "seller_id": "SEL-0001", "quantity_sold": q,
                       "sale_date": d.isoformat()} for pid, q, d in rows])
    history = ops.seller_history("SEL-0001", today - timedelta(days=29), 5)
    assert [(r["product_id"], r["units"], r["days_with_sales"]) for r in history] == \
        [("PRD-000002", 5, 1), ("PRD-000001", 3, 2)]
    assert history[1]["first_sale"] == (today - timedelta(days=2)).isoformat()
    assert history[1]["last_sale"] == today.isoformat()
    assert ops.top_products("SEL-0001", today - timedelta(days=29), 1) == [("PRD-000002", 5)]


# ---------------- with MySQL / PostgreSQL the app CALLs the routines ----------------
class RecordingCursor:
    def __init__(self, db):
        self.db = db

    def call(self, sql, params=()):
        self.db.calls.append((sql, tuple(params)))
        if self.db.fail:
            raise ProcedureError(self.db.fail)
        return self.db.rows

    def execute(self, sql, params=()):
        self.db.calls.append((sql, tuple(params)))
        self._rows = self.db.rows
        return self

    def fetchall(self):
        return self._rows


class RecordingDb:
    """Stands in for SqlDatabase(mysql|postgres): records every statement."""

    def __init__(self, dialect, rows=(), fail=None):
        self.dialect, self.rows, self.fail, self.calls = dialect, list(rows), fail, []

    def transaction(self):
        db = self

        class _Tx:
            def __enter__(self):
                return RecordingCursor(db)

            def __exit__(self, *exc):
                return False
        return _Tx()

    def query(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        return self.rows


def test_mysql_writes_go_through_the_procedures():
    db = RecordingDb("mysql")
    repo = MySQLFinanceRepository(db)
    assert repo.use_procedures
    repo.create_order(*order_rows(10001))
    repo.update_status(10001, "Pending", "Paid", NOW, "Approved",
                       {"invoice_id": 20001, "pdf_receipt_url": "/orders/10001/invoice", "issued_at": NOW})
    sql = [s for s, _ in db.calls]
    assert sql[0].startswith("CALL sp_create_order(") and sql[1].startswith("CALL sp_change_order_status(")
    assert all("%s" in s and "'" not in s for s in sql)               # values only as parameters
    params = db.calls[0][1]
    assert params[0] == "10001" and params[8] == "TRANSFER" and params[9] is None   # spei, no card digits
    assert '"unit_price": "500.00"' in params[3]                       # lines travel as JSON
    assert db.calls[1][1][:3] == ("10001", "PENDING", "PAID")


def test_mysql_cancel_and_history_call_their_procedures():
    db = RecordingDb("mysql", rows=[{"product_id": "PRD-000001", "quantity": 3}])
    repo = MySQLFinanceRepository(db)
    assert repo.cancel_order(10001, "Paid", NOW) == {"PRD-000001": 3}
    assert db.calls[-1][0].startswith("CALL sp_cancel_order(") and db.calls[-1][1][:2] == ("10001", "PAID")
    db.rows = [{"order_id": "10001", "created_at": NOW, "status": "CANCELLED", "total_amount": "1000.00",
                "line_count": 1, "units": 2, "orders_count": 1, "active_orders": 0, "total_spent": 0,
                "units_bought": 0}]
    h = repo.buyer_history("u-ana00001")
    assert db.calls[-1] == ("CALL sp_buyer_sales_history(%s)", ("u-ana00001",))
    assert h["orders"][0]["status"] == "Cancelled" and h["total_spent"] == 0


@pytest.mark.parametrize("code, error, text", [
    ("STALE_ORDER", StaleWrite, "changed meanwhile"),
    ("INVALID_TRANSITION", ValidationError, "not allowed"),
    ("CANNOT_CANCEL", ValidationError, "pending or paid"),
    ("INVALID_ORDER_ITEMS", ValidationError, "invalid items"),
    ("SOMETHING_NEW", ValidationError, "not allowed"),
])
def test_procedure_signals_become_friendly_errors(code, error, text):
    repo = MySQLFinanceRepository(RecordingDb("mysql", fail=code))
    with pytest.raises(error, match=text):
        repo.cancel_order(10001, "Paid", NOW)


def test_postgres_reads_through_the_functions():
    db = RecordingDb("postgres", rows=[{"product_id": "PRD-000001", "units": 4, "days_with_sales": 2,
                                        "first_sale": date(2026, 11, 1), "last_sale": date(2026, 11, 3)}])
    repo = PostgresOperationsRepository(db)
    assert repo.top_products("SEL-0001", date(2026, 11, 1), 3) == [("PRD-000001", 4)]
    assert db.calls[-1][0].startswith("SELECT product_id, units, days_with_sales, first_sale, last_sale "
                                      "FROM fn_seller_sales_history(%s, %s, %s)")
    db.rows = [{"sale_date": date(2026, 11, 3), "units": 4}]
    assert repo.units_by_day("SEL-0001", date(2026, 11, 1)) == {"2026-11-03": 4}
    assert "fn_seller_units_by_day(%s, %s)" in db.calls[-1][0]


class FakeMySQLError(Exception):
    pass


class FakePgError(Exception):
    sqlstate = "P0001"

    class diag:
        message_primary = "SOME_RULE"


def test_signal_errors_are_recognised():
    assert procedure_code(FakeMySQLError(1644, "STALE_ORDER")) == "STALE_ORDER"
    assert procedure_code(FakePgError("x")) == "SOME_RULE"
    assert procedure_code(FakeMySQLError(1062, "Duplicate entry")) is None
    hint = explain(Exception("PROCEDURE finance_db.sp_create_order does not exist"), "mysql")
    assert "install_procedures.py" in hint


# ---------------- the SQL files and the scripts ----------------
def test_procedure_files_define_every_routine():
    mysql = (ROOT / "database" / "finance_procedures.sql").read_text(encoding="utf-8")
    pg = POSTGRES_FILE.read_text(encoding="utf-8")
    for name in MYSQL_ROUTINES:
        assert f"CREATE PROCEDURE {name}(" in mysql and f"DROP PROCEDURE IF EXISTS {name} $$" in mysql
    for name in POSTGRES_ROUTINES:
        assert f"CREATE OR REPLACE FUNCTION {name}(" in pg
    assert mysql.count("START TRANSACTION") == 3 and mysql.count("ROLLBACK;") == 3
    assert mysql.count("WHERE order_id = p_order_id FOR UPDATE;") == 2   # status changes lock the row
    for text in (mysql, pg):                                   # no dynamic SQL anywhere
        assert "PREPARE " not in text and "EXECUTE " not in text.upper().replace("EXECUTE FUNCTION", "")


def test_installer_only_runs_procedure_statements():
    statements = mysql_statements()
    assert len(statements) == 2 * len(MYSQL_ROUTINES)
    assert all(s.split()[0] in ("DROP", "CREATE") and s.split()[1] == "PROCEDURE" for s in statements)
    assert all(not s.rstrip().endswith("$$") for s in statements)


def test_script_splitter_handles_delimiters_and_comments():
    text = "-- intro;\nSELECT 1;\nDELIMITER $$\nCREATE PROCEDURE p()\nBEGIN\n  SELECT 1; -- inner;\nEND $$\nDELIMITER ;\n"
    assert split_mysql_script(text) == ["SELECT 1", "CREATE PROCEDURE p()\nBEGIN\n  SELECT 1; -- inner;\nEND"]
    with pytest.raises(ValueError):
        split_mysql_script("SELECT 1")


def test_live_test_never_drops_the_real_database():
    statements = finance_ddl()
    assert statements and all(s.upper().startswith(("CREATE TABLE", "CREATE INDEX")) for s in statements)
    assert sum(s.upper().startswith("CREATE INDEX") for s in statements) == 3
    assert not any("DATABASE" in s.upper() or s.upper().startswith("USE") for s in statements)


# ---------------- the screens show the consolidated histories ----------------
def test_orders_page_shows_the_buyer_history(client, services):
    login(client, "ana@example.com")
    html = client.get("/orders").get_data(as_text=True)
    h = services.pay.my_history("u-ana00001")
    assert h.orders_count > 0 and "Total spent" in html and "Items bought" in html


def test_seller_dashboard_shows_days_with_sales(client):
    login(client, "carlos@urbanthread.mx")
    html = client.get("/seller").get_data(as_text=True)
    assert "Days with sales" in html and "Last sale" in html


def test_finance_indexes_cover_the_frequent_queries():
    from scripts.install_procedures import finance_indexes
    indexes = {name: sql for name, _, sql in finance_indexes()}
    assert indexes == {
        "idx_orders_buyer_created": "CREATE INDEX idx_orders_buyer_created ON orders (buyer_id, created_at)",
        "idx_orders_status_created": "CREATE INDEX idx_orders_status_created ON orders (status, created_at)",
        "idx_order_items_product": "CREATE INDEX idx_order_items_product ON order_items (product_id)"}


# ---------------- payment tokenization ----------------
def test_card_is_tokenized_and_its_number_discarded():
    import re
    from Interfaces.PayServiceInterface.PayDTO import CheckoutForm
    base = {"street": "Calle 1", "city": "Cuernavaca", "state": "Morelos", "zip_code": "62000"}
    card = dict(base, method="card", card_number="4242 4242 4242 4242", expiry="12/30", cvv="123")
    first, second = CheckoutForm.from_form(card), CheckoutForm.from_form(card)
    assert re.fullmatch(r"tok_[0-9a-f]{32}", first.card_token) and first.card_token != second.card_token
    assert first.card_last4 == "4242" and "4242424242424242" not in repr(first) + str(vars(first))
    assert not hasattr(first, "cvv") and not hasattr(first, "card_number")
    assert CheckoutForm.from_form(dict(base, method="cash")).card_token == ""


def test_stored_payment_has_token_and_never_the_card(finance):
    order, items, shipping, payment, history = order_rows(10001)
    finance.create_order(order, items, shipping,
                         dict(payment, payment_method="card", card_last4="4242",
                              card_token="tok_" + "a" * 32), history)
    stored = finance.get_order(10001)["payment"]
    assert stored["card_last4"] == "4242" and stored["card_token"] == "tok_" + "a" * 32
    assert "4242424242424242" not in str(finance.get_order(10001))


def test_create_order_procedure_receives_the_token():
    db = RecordingDb("mysql")
    order, items, shipping, payment, history = order_rows(10001)
    MySQLFinanceRepository(db).create_order(order, items, shipping,
                                            dict(payment, payment_method="card", card_last4="4242",
                                                 card_token="tok_" + "b" * 32), history)
    sql, params = db.calls[0]
    assert sql.count("%s") == 11 and params[8:] == ("CARD", "4242", "tok_" + "b" * 32)
    mysql = (ROOT / "database" / "finance_procedures.sql").read_text(encoding="utf-8")
    assert "INVALID_CARD_TOKEN" in mysql and "'^tok_[0-9a-f]{32}$'" in mysql


def test_new_columns_are_in_the_ddl_and_the_migration():
    from scripts.install_procedures import FINANCE_NEW_COLUMNS
    ddl = (ROOT / "database" / "finance_db.sql").read_text(encoding="utf-8")
    for table, column, sql in FINANCE_NEW_COLUMNS:
        assert f"    {column} " in ddl and sql.startswith(f"ALTER TABLE {table} ADD COLUMN {column} ")
        assert "NULL" in sql and "DROP" not in sql.upper()
