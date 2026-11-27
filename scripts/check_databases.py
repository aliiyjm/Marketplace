"""Check that every database the app is configured to use is reachable and has
the expected tables and columns. Run it on your computer before starting the app:

    python scripts/check_databases.py

It only READS (SELECT ... WHERE 1 = 0, COUNT(*) and the list of stored
procedures); it never changes data.
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from View.MainViews.Utils.env_file import load_env_file, utf8_console  # noqa: E402

utf8_console()      # Git Bash on Windows: ✓ / ✗ would crash a cp1252 console
ENV_FOUND = load_env_file()

FINANCE_TABLES = {
    "orders": ["order_id", "buyer_id", "created_at", "status", "subtotal", "tax_amount", "total_amount"],
    "order_items": ["item_id", "order_id", "product_id", "quantity", "unit_price", "size", "color"],
    "payments": ["payment_id", "order_id", "payment_method", "card_last4", "card_token", "status", "processed_at"],
    "shipping_addresses": ["address_id", "order_id", "street", "city", "state", "zip_code"],
    "invoices": ["invoice_id", "order_id", "pdf_receipt_url", "issued_at"],
    "order_status_history": ["history_id", "order_id", "old_status", "new_status", "changed_at"],
}
OPERATIONS_TABLES = {
    "categories": ["category_id", "parent_id", "name", "slug", "is_active"],
    "sellers": ["seller_id", "user_id", "store_name", "tax_id", "rating", "status", "created_at"],
    "reviews": ["review_id", "product_id", "buyer_id", "rating", "comment", "created_at"],
    "analytics": ["sale_id", "product_id", "category_id", "seller_id", "quantity_sold", "sale_date"],
    "alerts": ["alert_id", "product_id", "seller_id", "current_stock", "threshold_limit", "alert_status", "updated_at"],
}


def check_sql(db, tables, out=print):
    """Returns the number of problems found."""
    problems = 0
    for table, expected in tables.items():
        try:
            with db.transaction() as cur:
                cur.execute(f"SELECT * FROM {table} WHERE 1 = 0")      # fixed names from the dict above
                columns = {d[0].lower() for d in cur._cursor.description}
                count = int(cur.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])
        except Exception as e:
            detail = getattr(e, "detail", "") or str(e)
            if str(e).startswith("Could not connect"):     # no point trying the other tables
                out(f"  ✗ cannot connect: {detail}")
                return problems + 1
            out(f"  ✗ {table}: {detail}")
            problems += 1
            continue
        missing = [c for c in expected if c not in columns]
        if missing:
            out(f"  ✗ {table}: missing columns {', '.join(missing)} (run the corrected script)")
            problems += 1
        else:
            out(f"  ✓ {table} ({count} rows)")
    return problems


def check_routines(db, out=print):
    """The stored procedures/functions the app calls must exist."""
    from scripts.install_procedures import missing_routines
    try:
        missing = missing_routines(db)
    except Exception as e:
        out(f"  ✗ stored procedures: {getattr(e, 'detail', '') or e}")
        return 1
    if missing:
        out(f"  ✗ missing stored procedures: {', '.join(missing)}  ->  python scripts/install_procedures.py")
        return 1
    out("  ✓ stored procedures installed")
    return 0


def _release_route(client):
    """POST /productos/liberar of an order that does not exist: changes nothing.
    404 NO_RESERVATION means the Lambda route works."""
    from View.MainViews.Utils.product_api import ProductApiError
    try:
        client.release("0")
    except ProductApiError as e:
        if e.code == "NO_RESERVATION":
            return
        raise


def check_api(out=print):
    from View.MainViews.Utils.product_api import ProductApiClient, ProductApiError
    from View.MainViews.Utils.quick_api import QuickApiClient
    from View.MainViews.Utils import api_client
    problems = 0
    client = ProductApiClient()
    checks = [
        ("products  GET /productos/consultar", lambda: client.list_page(limit=1)),
        ("users     GET /usuarios/consultar", lambda: api_client.get_user("u-ana00001")),
        ("carts     GET /carrito/consultar", lambda: QuickApiClient().get_cart("u-ana00001")),
        ("stock     POST /productos/liberar (atomic transactions)", lambda: _release_route(client)),
    ]
    for label, call in checks:
        try:
            call()
            out(f"  ✓ {label}")
        except (ProductApiError, api_client.ApiError) as e:
            out(f"  ✗ {label}: {e}")
            problems += 1
    return problems


def main():
    from Implementation.DataSource.SqlConnection import mysql_from_env, postgres_from_env
    problems = 0
    backends = {k: os.environ.get(k, "memory") for k in
                ("FINANCE_BACKEND", "OPERATIONS_BACKEND", "DATA_BACKEND", "PRODUCTS_BACKEND")}
    if not ENV_FOUND:
        print(f"✗ No .env file in {ROOT}. Copy .env.example as .env and fill it in.")
    print("Configuration:", ", ".join(f"{k}={v}" for k, v in backends.items()))
    if all(v == "memory" for v in backends.values()):
        print("  (everything is 'memory': the app is not using any database; check your .env)")
    if backends["FINANCE_BACKEND"] == "mysql":
        print(f"MySQL {os.environ.get('MYSQL_DATABASE', 'finance_db')} @ {os.environ.get('MYSQL_HOST', 'localhost')}")
        db = mysql_from_env()
        found = check_sql(db, FINANCE_TABLES)
        problems += found or check_routines(db)
    if backends["OPERATIONS_BACKEND"] == "postgres":
        print(f"PostgreSQL {os.environ.get('PG_DATABASE', 'operations_db')} @ {os.environ.get('PG_HOST', 'localhost')}")
        db = postgres_from_env()
        found = check_sql(db, OPERATIONS_TABLES)
        problems += found or check_routines(db)
    if "aws" in (backends["DATA_BACKEND"], backends["PRODUCTS_BACKEND"]):
        print("API Gateway + Lambda (DynamoDB MarketplaceDB)")
        problems += check_api()
    print("\nAll good." if not problems else f"\n{problems} problem(s) found.")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, ValueError) as e:   # missing driver, bad port number...: no traceback
        print(f"✗ {e}")
        sys.exit(1)
