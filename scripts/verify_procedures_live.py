"""Test the stored procedures on your REAL servers without touching your data:

    python scripts/verify_procedures_live.py

- MySQL: creates a temporary DATABASE sp_test_<random>, creates the tables of
  database/finance_db.sql and the procedures of finance_procedures.sql in it,
  runs the scenarios and DROPS only that temporary database.
- PostgreSQL: the same with a temporary SCHEMA sp_test_<random>.

Your finance_db / postgres data are never read or changed. The app's own
repositories are used, so this proves the app <-> procedure contract too.
Needs the same .env as the app (the RDS master user can create databases).
"""
import os
import secrets
import sys
import threading
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from View.MainViews.Utils.env_file import load_env_file, utf8_console  # noqa: E402

RESULTS = []


def check(label, condition, detail=""):
    RESULTS.append(bool(condition))
    print(f"  {'✓' if condition else '✗'} {label}" + (f"  ({detail})" if detail and not condition else ""))


def raises(fn, error_type, text=""):
    try:
        fn()
    except error_type as e:
        return text.lower() in str(e).lower()
    except Exception:
        return False
    return False


def finance_ddl():
    """CREATE TABLE / CREATE INDEX statements of finance_db.sql WITHOUT its DROP/CREATE/USE DATABASE lines."""
    text = (ROOT / "database" / "finance_db.sql").read_text(encoding="utf-8")
    code = "\n".join(line.split("--", 1)[0] for line in text.splitlines())   # drop every -- comment
    statements = [s.strip() for s in code.split(";") if s.strip()]
    keep = [s for s in statements if not s.upper().startswith(("DROP DATABASE", "CREATE DATABASE", "USE "))]
    for s in keep:
        if not s.upper().startswith(("CREATE TABLE", "CREATE INDEX")):
            raise SystemExit(f"Refusing to run an unexpected statement: {s[:60]}")
    return keep


# ======================================================================
def run_mysql():
    import pymysql
    from Implementation.DataSource.MySQLFinanceRepository import MySQLFinanceRepository
    from Implementation.DataSource.SqlConnection import SqlDatabase
    from Interfaces.DataInterfaces.RepositoryErrors import DuplicateKey, StaleWrite
    from Interfaces.Validators import ValidationError
    from scripts.install_procedures import install_mysql, missing_routines

    base = dict(host=os.environ.get("MYSQL_HOST", "localhost"), port=int(os.environ.get("MYSQL_PORT", "3306")),
                user=os.environ.get("MYSQL_USER", "root"), password=os.environ.get("MYSQL_PASSWORD", ""),
                charset="utf8mb4", autocommit=False, connect_timeout=5)
    temp = f"sp_test_{secrets.token_hex(4)}"            # letters/digits only: safe as a name
    print(f"MySQL @ {base['host']}  (temporary database {temp})")
    admin = pymysql.connect(**base)
    try:
        with admin.cursor() as cur:
            cur.execute(f"CREATE DATABASE `{temp}`")
        db = SqlDatabase(lambda: pymysql.connect(database=temp, **base), "mysql", f"MySQL {temp}")
        with db.transaction() as cur:
            for statement in finance_ddl():
                cur._cursor.execute(statement)
        install_mysql(db, out=lambda m: None)
        check("4 procedures created", not missing_routines(db))

        repo = MySQLFinanceRepository(db)
        check("the app uses CALL with MySQL", repo.use_procedures)
        now = datetime(2026, 11, 26, 10, 0, 0)

        def order(oid, items, method="card", last4="4242", buyer="u-test0001"):
            repo.create_order({"order_id": oid, "buyer_id": buyer, "created_at": now.isoformat(),
                               "status": "Pending", "subtotal": 0, "tax_amount": 0, "total_amount": 0},
                              items, {"street": "Av. Universidad 1", "city": "Emiliano Zapata",
                                      "state": "Morelos", "zip_code": "62765"},
                              {"payment_id": f"PAY-{oid}", "payment_method": method, "card_last4": last4,
                               "status": "Awaiting payment"},
                              {"old_status": None, "new_status": "Pending", "changed_at": now.isoformat()})

        lines = [{"product_id": "PRD-000001", "quantity": 1, "unit_price": Decimal("599.00"), "size": "M", "color": "Navy"},
                 {"product_id": "PRD-000002", "quantity": 2, "unit_price": Decimal("250.50"), "size": "S", "color": "Red"},
                 {"product_id": "PRD-000001", "quantity": 1, "unit_price": Decimal("599.00"), "size": "L", "color": "Navy"}]
        print(" sp_create_order")
        order(20001, lines)
        full = repo.get_order(20001)
        o = full["order"]
        check("order, items, shipping, payment and history written",
              o["status"] == "Pending" and len(full["items"]) == 3 and full["shipping"] and full["payment"]
              and len(full["history"]) == 1)
        check("totals and IVA computed by the procedure",
              (o["total_amount"], o["subtotal"], o["tax_amount"]) ==
              (Decimal("1699.00"), Decimal("1464.66"), Decimal("234.34")),
              f"{o['total_amount']} {o['subtotal']} {o['tax_amount']}")
        check("only the last 4 card digits are stored", full["payment"]["card_last4"] == "4242")
        check("same order number again -> DuplicateKey", raises(lambda: order(20001, lines), DuplicateKey))
        bad = [dict(lines[0], quantity=0)]
        check("invalid quantity -> rejected", raises(lambda: order(20002, bad), ValidationError, "invalid items"))
        check("card without last 4 digits -> rejected",
              raises(lambda: order(20003, lines, last4=None), ValidationError, "card"))
        check("rejected orders leave nothing behind", repo.get_order(20002) is None and repo.get_order(20003) is None)
        order(20004, lines[:1], method="cash", last4=None)
        check("cash order has no card digits", not repo.get_order(20004)["payment"]["card_last4"])

        print(" sp_change_order_status")
        repo.update_status(20001, "Pending", "Paid", now.isoformat(),
                           invoice={"invoice_id": 30001, "pdf_receipt_url": "/orders/20001/invoice",
                                    "issued_at": now.isoformat()})
        full = repo.get_order(20001)
        check("PENDING -> PAID approves the payment and issues the invoice",
              full["order"]["status"] == "Paid" and full["payment"]["status"] == "Approved" and full["invoice"])
        check("stale status -> StaleWrite",
              raises(lambda: repo.update_status(20001, "Pending", "Paid", now.isoformat()), StaleWrite))
        check("PAID -> DELIVERED is not allowed",
              raises(lambda: repo.update_status(20001, "Paid", "Delivered", now.isoformat()), ValidationError, "not allowed"))

        print(" sp_cancel_order")
        back = repo.cancel_order(20001, "Paid", (now + timedelta(hours=1)).isoformat())
        full = repo.get_order(20001)
        check("returns the units to restock (grouped per product)", back == {"PRD-000001": 2, "PRD-000002": 2}, back)
        check("PAID order is cancelled and refunded",
              full["order"]["status"] == "Cancelled" and full["payment"]["status"] == "Refunded"
              and [h["new_status"] for h in full["history"]] == ["Pending", "Paid", "Cancelled"])
        check("cancelling twice is refused",
              raises(lambda: repo.cancel_order(20001, "Cancelled", now.isoformat()), ValidationError))

        order(20005, lines[:2])
        outcomes = []

        def cancel():
            try:
                repo.cancel_order(20005, "Pending", now.isoformat())
                outcomes.append("ok")
            except StaleWrite:
                outcomes.append("stale")
            except Exception as e:      # noqa: BLE001 - reported below
                outcomes.append(repr(e))
        threads = [threading.Thread(target=cancel) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        check("two admins cancel at the same time -> only one wins (row lock)",
              sorted(outcomes) == ["ok", "stale"], outcomes)
        check("a PENDING order is cancelled without refund",
              repo.get_order(20005)["payment"]["status"] == "Cancelled")

        print(" sp_buyer_sales_history")
        h = repo.buyer_history("u-test0001")
        check("consolidated history of the buyer",
              h["orders_count"] == 3 and h["active_orders"] == 1 and h["total_spent"] == Decimal("599.00")
              and h["units_bought"] == 1, {k: v for k, v in h.items() if k != "orders"})
        check("unknown buyer -> empty history", repo.buyer_history("nobody")["orders_count"] == 0)

        print(" database/finance_seed.sql (example data through the procedures)")
        seed = (ROOT / "database" / "finance_seed.sql").read_text(encoding="utf-8")
        code = "\n".join(line.split("--", 1)[0] for line in seed.splitlines())
        with db.transaction() as cur:
            for statement in (x.strip() for x in code.split(";") if x.strip()):
                cur.call(statement)
        h = repo.buyer_history("u-ana00001")
        check("3 example orders, 2 not cancelled, $2,796.00 spent",
              (h["orders_count"], h["active_orders"], h["total_spent"]) == (3, 2, Decimal("2796.00")),
              {k: v for k, v in h.items() if k != "orders"})
        check("example order 90001 is SHIPPED with its invoice",
              repo.get_order(90001)["order"]["status"] == "Shipped" and repo.get_order(90001)["invoice"])
    finally:
        with admin.cursor() as cur:
            cur.execute(f"DROP DATABASE IF EXISTS `{temp}`")
        admin.close()
        print(f"  (temporary database {temp} deleted)")


# ======================================================================
def run_postgres():
    import psycopg
    from Implementation.DataSource.PostgresOperationsRepository import PostgresOperationsRepository
    from Implementation.DataSource.SqlConnection import SqlDatabase
    from scripts.install_procedures import POSTGRES_FILE, missing_routines

    base = dict(host=os.environ.get("PG_HOST", "localhost"), port=int(os.environ.get("PG_PORT", "5432")),
                user=os.environ.get("PG_USER", "postgres"), password=os.environ.get("PG_PASSWORD", ""),
                dbname=os.environ.get("PG_DATABASE", "operations_db"), connect_timeout=5)
    temp = f"sp_test_{secrets.token_hex(4)}"
    print(f"PostgreSQL @ {base['host']}  (temporary schema {temp})")
    # Every connection only sees the temporary schema: the real tables are not in its search_path.
    in_temp = dict(base, options=f"-c search_path={temp}")
    with psycopg.connect(**base, autocommit=True) as admin:
        admin.execute(f'CREATE SCHEMA "{temp}"')
    try:
        db = SqlDatabase(lambda: psycopg.connect(**in_temp), "postgres", f"PostgreSQL {temp}")
        with db.transaction() as cur:
            schema = cur.execute("SELECT current_schema() AS s").fetchone()["s"]
            if schema != temp:
                raise SystemExit(f"Refusing to continue: current schema is {schema}, not {temp}")
            cur._cursor.execute((ROOT / "database" / "operations_db.sql").read_text(encoding="utf-8"))
            cur._cursor.execute(POSTGRES_FILE.read_text(encoding="utf-8"))
        check("2 functions created", not missing_routines(db))
        today = date(2026, 11, 26)
        with db.transaction() as cur:
            cur.execute("INSERT INTO categories (category_id, parent_id, name, slug) VALUES (1, NULL, 'Men', 'men')")
            cur.execute("INSERT INTO sellers (seller_id, user_id, store_name, tax_id) "
                        "VALUES ('SEL-0001', 'u-seller01', 'Urban Thread', 'UTH850101AB1')")
            rows = [("PRD-000001", 2, today), ("PRD-000001", 1, today - timedelta(days=1)),
                    ("PRD-000002", 5, today), ("PRD-000003", 1, today - timedelta(days=40))]
            for pid, qty, day in rows:
                cur.execute("INSERT INTO analytics (product_id, category_id, seller_id, quantity_sold, sale_date) "
                            "VALUES (%s, 1, 'SEL-0001', %s, %s)", (pid, qty, day))
        repo = PostgresOperationsRepository(db)
        since = today - timedelta(days=29)
        history = repo.seller_history("SEL-0001", since, 5)
        check("fn_seller_sales_history: best sellers first, old sales excluded",
              [(r["product_id"], r["units"], r["days_with_sales"]) for r in history]
              == [("PRD-000002", 5, 1), ("PRD-000001", 3, 2)], history)
        check("fn_seller_sales_history: first and last sale",
              history[1]["first_sale"] == (today - timedelta(days=1)).isoformat()
              and history[1]["last_sale"] == today.isoformat())
        check("fn_seller_units_by_day", repo.units_by_day("SEL-0001", since) ==
              {today.isoformat(): 7, (today - timedelta(days=1)).isoformat(): 1})
        check("top_products uses the function", repo.top_products("SEL-0001", since, 1) == [("PRD-000002", 5)])
        check("another seller sees nothing", repo.seller_history("SEL-0099", since) == [])

        print(" database/operations_seed.sql (initial data)")
        with psycopg.connect(**base, autocommit=True) as admin:
            admin.execute(f'CREATE SCHEMA "{temp}_seed"')
        seed_db = SqlDatabase(lambda: psycopg.connect(**dict(base, options=f"-c search_path={temp}_seed")),
                              "postgres", f"PostgreSQL {temp}_seed")
        seed = (ROOT / "database" / "operations_seed.sql").read_text(encoding="utf-8")
        body = "\n".join(line for line in seed.splitlines() if line.strip() not in ("BEGIN;", "COMMIT;"))
        with seed_db.transaction() as cur:
            if cur.execute("SELECT current_schema() AS s").fetchone()["s"] != f"{temp}_seed":
                raise SystemExit("Refusing to continue: wrong schema for the seed test")
            cur._cursor.execute((ROOT / "database" / "operations_db.sql").read_text(encoding="utf-8"))
            cur._cursor.execute(body)
            counts = {t: int(cur.execute(f"SELECT COUNT(*) AS n FROM {t}").fetchone()["n"])
                      for t in ("categories", "sellers", "reviews", "analytics", "alerts")}
        check("seed loads with every constraint", counts["categories"] == 29 and counts["sellers"] == 12
              and counts["reviews"] > 27000, counts)
    finally:
        with psycopg.connect(**base, autocommit=True) as admin:
            admin.execute(f'DROP SCHEMA IF EXISTS "{temp}" CASCADE')
            admin.execute(f'DROP SCHEMA IF EXISTS "{temp}_seed" CASCADE')
        print(f"  (temporary schemas {temp} and {temp}_seed deleted)")


def main():
    utf8_console()
    load_env_file()
    ran = False
    if os.environ.get("FINANCE_BACKEND") == "mysql":
        run_mysql()
        ran = True
    if os.environ.get("OPERATIONS_BACKEND") == "postgres":
        run_postgres()
        ran = True
    if not ran:
        print("✗ Set FINANCE_BACKEND=mysql and/or OPERATIONS_BACKEND=postgres in .env first.")
        sys.exit(1)
    failed = RESULTS.count(False)
    print(f"\n{len(RESULTS) - failed}/{len(RESULTS)} checks passed." if not failed
          else f"\n{failed} of {len(RESULTS)} checks FAILED.")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
