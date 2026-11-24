"""Operational data in PostgreSQL (tables of the operations script):
categories, sellers, reviews, analytics, alerts.

Statuses follow the script's conventions (sellers ACTIVE/SUSPENDED, alerts
OPEN/RESOLVED); this class translates them to the names the app uses.
"""
from Implementation.DataSource.SqlConnection import as_date, as_datetime, as_iso
from Interfaces.DataInterfaces.OperationsRepositoryInterface import OperationsRepositoryInterface
from Interfaces.DataInterfaces.RepositoryErrors import DuplicateKey

SELLER_STATUS = {"active": "ACTIVE", "suspended": "SUSPENDED"}
ALERT_STATUS = {"Open": "OPEN", "Resolved": "RESOLVED"}
ALERT_COLUMNS = ("current_stock", "threshold_limit", "alert_status", "updated_at")


def _from_db(mapping, value):
    reverse = {v: k for k, v in mapping.items()}
    return reverse.get(str(value).upper(), value)


class PostgresOperationsRepository(OperationsRepositoryInterface):
    def __init__(self, db):
        self.db = db               # SqlDatabase (postgres)

    # ---------- categories ----------
    def list_categories(self):
        rows = self.db.query("SELECT category_id, parent_id, name, slug, is_active FROM categories "
                             "ORDER BY category_id")
        return [{"category_id": int(r["category_id"]),
                 "parent_id": int(r["parent_id"]) if r["parent_id"] is not None else None,
                 "name": r["name"], "slug": r["slug"], "is_active": bool(r["is_active"])} for r in rows]

    def create_category(self, record):
        with self.db.transaction() as cur:
            row = cur.execute("INSERT INTO categories (parent_id, name, slug, is_active) "
                              "VALUES (%s, %s, %s, %s) RETURNING category_id",
                              (record["parent_id"], record["name"], record["slug"], record["is_active"])).fetchone()
            return int(row["category_id"])

    def update_category(self, category_id, record):
        with self.db.transaction() as cur:
            cur.execute("UPDATE categories SET parent_id = %s, name = %s, slug = %s, is_active = %s "
                        "WHERE category_id = %s",
                        (record["parent_id"], record["name"], record["slug"], record["is_active"], category_id))

    # ---------- sellers ----------
    def list_sellers(self):
        rows = self.db.query("SELECT seller_id, user_id, store_name, tax_id, rating, status, created_at "
                             "FROM sellers ORDER BY seller_id")
        return [{"seller_id": r["seller_id"], "user_id": r["user_id"], "store_name": r["store_name"],
                 "tax_id": r["tax_id"], "rating": float(r["rating"] or 0),
                 "status": _from_db(SELLER_STATUS, r["status"]), "created_at": as_iso(r["created_at"])}
                for r in rows]

    def create_seller(self, record):
        for _ in range(3):   # two registrations at the same time could pick the same number
            rows = self.db.query("SELECT seller_id FROM sellers")
            numbers = [int(r["seller_id"][4:]) for r in rows if str(r["seller_id"])[4:].isdigit()]
            seller_id = f"SEL-{max(numbers + [0]) + 1:04d}"
            try:
                with self.db.transaction() as cur:
                    cur.execute("INSERT INTO sellers (seller_id, user_id, store_name, tax_id, rating, status, "
                                "created_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                                (seller_id, record["user_id"], record["store_name"], record["tax_id"],
                                 record.get("rating") or 0, SELLER_STATUS.get(record.get("status", "active")),
                                 as_datetime(record.get("created_at"))))
                return seller_id
            except DuplicateKey:
                if any(s["user_id"] == record["user_id"] for s in self.list_sellers()):
                    raise DuplicateKey("This account already has a store.") from None
        raise DuplicateKey("Could not create the store. Please try again.")

    def set_seller_status(self, seller_id, status):
        with self.db.transaction() as cur:
            cur.execute("UPDATE sellers SET status = %s WHERE seller_id = %s",
                        (SELLER_STATUS.get(status, status), seller_id))

    # ---------- reviews ----------
    def reviews_for(self, product_id, limit=5):
        rows = self.db.query("SELECT review_id, product_id, buyer_id, rating, comment, created_at FROM reviews "
                             "WHERE product_id = %s ORDER BY created_at DESC, review_id DESC LIMIT %s",
                             (product_id, limit))
        return [dict(r, review_id=int(r["review_id"]), rating=int(r["rating"]),
                     created_at=as_iso(r["created_at"])) for r in rows]

    def rating_counts(self, product_id):
        rows = self.db.query("SELECT rating, COUNT(*) AS n FROM reviews WHERE product_id = %s GROUP BY rating",
                             (product_id,))
        counts = {stars: 0 for stars in range(1, 6)}
        for r in rows:
            counts[int(r["rating"])] = int(r["n"])
        return counts

    def rating_stats(self):
        rows = self.db.query("SELECT product_id, AVG(rating) AS avg, COUNT(*) AS n FROM reviews GROUP BY product_id")
        return {r["product_id"]: (float(r["avg"]), int(r["n"])) for r in rows}

    def has_reviewed(self, buyer_id, product_id):
        return self.db.query_one("SELECT 1 AS ok FROM reviews WHERE buyer_id = %s AND product_id = %s LIMIT 1",
                                 (buyer_id, product_id)) is not None

    def add_review(self, record):
        with self.db.transaction() as cur:
            row = cur.execute("INSERT INTO reviews (product_id, buyer_id, rating, comment, created_at) "
                              "VALUES (%s, %s, %s, %s, %s) RETURNING review_id",
                              (record["product_id"], record["buyer_id"], record["rating"], record["comment"],
                               as_datetime(record["created_at"]))).fetchone()
            return int(row["review_id"])

    # ---------- analytics ----------
    def record_sales(self, rows):
        if not rows:
            return
        with self.db.transaction() as cur:
            for r in rows:
                cur.execute("INSERT INTO analytics (product_id, category_id, seller_id, quantity_sold, sale_date) "
                            "VALUES (%s, %s, %s, %s, %s)",
                            (r["product_id"], r["category_id"], r["seller_id"], r["quantity_sold"],
                             as_date(r["sale_date"])))

    def units_by_day(self, seller_id, since):
        rows = self.db.query("SELECT sale_date, SUM(quantity_sold) AS units FROM analytics "
                             "WHERE seller_id = %s AND sale_date >= %s GROUP BY sale_date",
                             (seller_id, as_date(since)))
        return {as_iso(r["sale_date"])[:10]: int(r["units"]) for r in rows}

    def top_products(self, seller_id, since, limit=5):
        rows = self.db.query("SELECT product_id, SUM(quantity_sold) AS units FROM analytics "
                             "WHERE seller_id = %s AND sale_date >= %s GROUP BY product_id "
                             "ORDER BY units DESC, product_id LIMIT %s",
                             (seller_id, as_date(since), limit))
        return [(r["product_id"], int(r["units"])) for r in rows]

    # ---------- alerts ----------
    @staticmethod
    def _alert(r):
        return {"alert_id": int(r["alert_id"]), "product_id": r["product_id"], "seller_id": r["seller_id"],
                "current_stock": int(r["current_stock"]), "threshold_limit": int(r["threshold_limit"]),
                "alert_status": _from_db(ALERT_STATUS, r["alert_status"]), "updated_at": as_iso(r["updated_at"])}

    def open_alert(self, product_id):
        row = self.db.query_one("SELECT * FROM alerts WHERE product_id = %s AND alert_status = %s "
                                "ORDER BY alert_id DESC LIMIT 1", (product_id, ALERT_STATUS["Open"]))
        return self._alert(row) if row else None

    def insert_alert(self, record):
        with self.db.transaction() as cur:
            row = cur.execute("INSERT INTO alerts (product_id, seller_id, current_stock, threshold_limit, "
                              "alert_status, updated_at) VALUES (%s, %s, %s, %s, %s, %s) RETURNING alert_id",
                              (record["product_id"], record["seller_id"], record["current_stock"],
                               record["threshold_limit"], ALERT_STATUS[record["alert_status"]],
                               as_datetime(record["updated_at"]))).fetchone()
            return int(row["alert_id"])

    def update_alert(self, alert_id, fields):
        columns = [c for c in ALERT_COLUMNS if c in fields]      # whitelist: column names never come from input
        if not columns:
            return
        values = []
        for c in columns:
            value = fields[c]
            if c == "alert_status":
                value = ALERT_STATUS[value]
            elif c == "updated_at":
                value = as_datetime(value)
            values.append(value)
        sets = ", ".join(f"{c} = %s" for c in columns)
        with self.db.transaction() as cur:
            cur.execute(f"UPDATE alerts SET {sets} WHERE alert_id = %s", (*values, alert_id))

    def alerts_for_seller(self, seller_id, limit=6):
        rows = self.db.query("SELECT * FROM alerts WHERE seller_id = %s "
                             "ORDER BY CASE WHEN alert_status = %s THEN 0 ELSE 1 END, current_stock, alert_id "
                             "LIMIT %s", (seller_id, ALERT_STATUS["Open"], limit))
        return [self._alert(r) for r in rows]

    def count_open_alerts(self, seller_id):
        row = self.db.query_one("SELECT COUNT(*) AS n FROM alerts WHERE seller_id = %s AND alert_status = %s",
                                (seller_id, ALERT_STATUS["Open"]))
        return int(row["n"]) if row else 0
