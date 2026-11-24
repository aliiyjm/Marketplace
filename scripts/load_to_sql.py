"""Load the demo data into PostgreSQL: categories, sellers, reviews, sales
history (analytics) and stock alerts. MySQL finance_db starts empty: the app
creates the orders.

The data is generated with the same fixed seed as the products and users
uploaded to DynamoDB (scripts/load_to_dynamo.py), so product, seller and user
ids always match. Use the same --count in both scripts.

Usage (from the project root, with the PG_* settings in .env):
    python scripts/load_to_sql.py --dry-run     # only counts the rows
    python scripts/load_to_sql.py               # loads (refuses if there is data already)
    python scripts/load_to_sql.py --replace     # deletes the demo tables' rows and loads again
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from View.MainViews.Utils.env_file import load_env_file  # noqa: E402

load_env_file()

from Implementation.DataSource.MemoryStore import MemoryStore  # noqa: E402
from Implementation.DataSource.PostgresOperationsRepository import ALERT_STATUS, SELLER_STATUS  # noqa: E402
from Implementation.DataSource.Seed import seed  # noqa: E402
from Implementation.DataSource.SqlConnection import as_date, as_datetime, postgres_from_env  # noqa: E402

TABLES = ("alerts", "analytics", "reviews", "sellers", "categories")   # delete order (foreign keys)


def build_rows(count):
    store = MemoryStore()
    seed(store, product_count=count)
    categories = sorted(store.categories.values(), key=lambda c: (c["parent_id"] is not None, c["category_id"]))
    return {
        "categories": [(c["category_id"], c["parent_id"], c["name"], c["slug"], c["is_active"]) for c in categories],
        "sellers": [(x["seller_id"], x["user_id"], x["store_name"], x["tax_id"], x["rating"],
                     SELLER_STATUS[x["status"]], as_datetime(x["created_at"])) for x in store.sellers.values()],
        "reviews": [(r["product_id"], r["buyer_id"], r["rating"], r["comment"], as_datetime(r["created_at"]))
                    for r in sorted(store.reviews.values(), key=lambda r: r["review_id"])],
        "analytics": [(a["product_id"], a["category_id"], a["seller_id"], a["quantity_sold"], as_date(a["sale_date"]))
                      for a in store.analytics],
        "alerts": [(a["product_id"], a["seller_id"], a["current_stock"], a["threshold_limit"],
                    ALERT_STATUS[a["alert_status"]], as_datetime(a["updated_at"])) for a in store.alerts.values()],
    }


def load(db, rows, replace=False, out=print):
    with db.transaction() as cur:     # all or nothing
        existing = int(cur.execute("SELECT COUNT(*) AS n FROM categories").fetchone()["n"])
        if existing and not replace:
            raise SystemExit("PostgreSQL already has categories. Use --replace to delete the demo data and load again.")
        if replace:
            for table in TABLES:
                cur.execute(f"DELETE FROM {table}")       # fixed table names, never user input
        cur.executemany("INSERT INTO categories (category_id, parent_id, name, slug, is_active) "
                        "VALUES (%s, %s, %s, %s, %s)", rows["categories"])
        if db.dialect == "postgres":   # explicit ids: move the SERIAL counter past them
            cur.execute("SELECT setval(pg_get_serial_sequence('categories', 'category_id'), "
                        "(SELECT MAX(category_id) FROM categories))")
        cur.executemany("INSERT INTO sellers (seller_id, user_id, store_name, tax_id, rating, status, created_at) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s)", rows["sellers"])
        cur.executemany("INSERT INTO reviews (product_id, buyer_id, rating, comment, created_at) "
                        "VALUES (%s, %s, %s, %s, %s)", rows["reviews"])
        cur.executemany("INSERT INTO analytics (product_id, category_id, seller_id, quantity_sold, sale_date) "
                        "VALUES (%s, %s, %s, %s, %s)", rows["analytics"])
        cur.executemany("INSERT INTO alerts (product_id, seller_id, current_stock, threshold_limit, alert_status, "
                        "updated_at) VALUES (%s, %s, %s, %s, %s, %s)", rows["alerts"])
    out("Loaded: " + ", ".join(f"{len(rows[t])} {t}" for t in reversed(TABLES)))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--count", type=int, default=17000, help="same number of products as in DynamoDB")
    parser.add_argument("--replace", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    rows = build_rows(args.count)
    if args.dry_run:
        print("Would load: " + ", ".join(f"{len(rows[t])} {t}" for t in reversed(TABLES)))
        return
    load(postgres_from_env(), rows, replace=args.replace)


if __name__ == "__main__":
    main()
