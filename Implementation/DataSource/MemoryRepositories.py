"""In-memory repositories (default, tests and offline demo).

They keep the data in the MemoryStore dictionaries, so the app works without
any database. The MySQL / PostgreSQL / DynamoDB repositories implement the
same contracts, so the business logic does not change when they are switched on.
"""
import copy
from collections import Counter

from Interfaces.DataInterfaces.FinanceRepositoryInterface import (FinanceRepositoryInterface,
                                                                  history_summary)
from Interfaces.DataInterfaces.OperationsRepositoryInterface import OperationsRepositoryInterface
from Interfaces.DataInterfaces.QuickRepositoryInterface import QuickRepositoryInterface
from Interfaces.DataInterfaces.RepositoryErrors import DuplicateKey, StaleWrite
from Interfaces.Validators import ValidationError


class MemoryFinanceRepository(FinanceRepositoryInterface):
    is_memory = True

    def __init__(self, store):
        self.store = store

    def next_order_id(self):
        return self.store.next_id("orders", start=10001)

    def create_order(self, order, items, shipping, payment, history):
        s = self.store
        oid = order["order_id"]
        with s.lock:
            if oid in s.orders:
                raise DuplicateKey("That order number is already used.")
            s.orders[oid] = dict(order)
            s.order_items[oid] = [dict(i, item_id=s.next_id("order_items"), order_id=oid) for i in items]
            s.shipping[oid] = dict(shipping, address_id=s.next_id("shipping"), order_id=oid)
            s.payments[oid] = dict(payment, order_id=oid)
            s.status_history[oid] = [dict(history, history_id=s.next_id("status_history"), order_id=oid)]

    def update_status(self, order_id, old_status, new_status, changed_at, payment_status=None, invoice=None):
        s = self.store
        with s.lock:
            order = s.orders.get(order_id)
            if not order or order["status"] != old_status:
                raise StaleWrite("The order changed meanwhile. Reload the page and try again.")
            order["status"] = new_status
            s.status_history[order_id].append({"history_id": s.next_id("status_history"), "order_id": order_id,
                                               "old_status": old_status, "new_status": new_status,
                                               "changed_at": changed_at})
            if payment_status and order_id in s.payments:
                s.payments[order_id]["status"] = payment_status
            if invoice:
                s.invoices[order_id] = dict(invoice, order_id=order_id)

    def cancel_order(self, order_id, old_status, changed_at):
        """Same rules as sp_cancel_order."""
        s = self.store
        with s.lock:
            order = s.orders.get(order_id)
            if not order:
                raise ValidationError("Order not found.")
            if order["status"] != old_status:
                raise StaleWrite("The order changed meanwhile. Reload the page and try again.")
            if old_status not in ("Pending", "Paid"):
                raise ValidationError(f"An order that is {old_status} cannot be cancelled.")
            self.update_status(order_id, old_status, "Cancelled", changed_at,
                               "Refunded" if old_status == "Paid" else "Cancelled")
            returned = {}
            for item in s.order_items.get(order_id, []):
                returned[item["product_id"]] = returned.get(item["product_id"], 0) + item["quantity"]
            return returned

    def buyer_history(self, buyer_id):
        """Same result as sp_buyer_sales_history."""
        s = self.store
        rows = []
        for o in sorted((o for o in s.orders.values() if o["buyer_id"] == buyer_id),
                        key=lambda o: (o["created_at"], o["order_id"]), reverse=True):
            items = s.order_items.get(o["order_id"], [])
            rows.append({"order_id": o["order_id"], "created_at": o["created_at"], "status": o["status"],
                         "total_amount": o["total_amount"], "line_count": len(items),
                         "units": sum(i["quantity"] for i in items)})
        return history_summary(rows)

    def _full(self, o):
        s = self.store
        oid = o["order_id"]
        return {"order": dict(o), "items": copy.deepcopy(s.order_items.get(oid, [])),
                "history": copy.deepcopy(s.status_history.get(oid, [])),
                "payment": dict(s.payments[oid]) if oid in s.payments else None,
                "shipping": dict(s.shipping[oid]) if oid in s.shipping else None,
                "invoice": dict(s.invoices[oid]) if oid in s.invoices else None}

    def get_order(self, order_id):
        order = self.store.orders.get(order_id)
        return self._full(order) if order else None

    def list_orders(self, buyer_id=None, status=None, order_id_contains=None):
        rows = list(self.store.orders.values())
        if buyer_id is not None:
            rows = [o for o in rows if o["buyer_id"] == buyer_id]
        if status:
            rows = [o for o in rows if o["status"] == status]
        if order_id_contains:
            rows = [o for o in rows if order_id_contains in str(o["order_id"])]
        rows.sort(key=lambda o: o["created_at"], reverse=True)
        return [self._full(o) for o in rows]

    def has_bought(self, buyer_id, product_id):
        s = self.store
        return any(o["buyer_id"] == buyer_id and o["status"] != "Cancelled"
                   and any(i["product_id"] == product_id for i in s.order_items.get(o["order_id"], []))
                   for o in s.orders.values())


class MemoryOperationsRepository(OperationsRepositoryInterface):
    is_memory = True

    def __init__(self, store):
        self.store = store

    # ---------- categories ----------
    def list_categories(self):
        return [dict(c) for c in self.store.categories.values()]

    def create_category(self, record):
        s = self.store
        with s.lock:
            if s.category_by_slug(record["slug"]):
                raise DuplicateKey("That slug is already in use.")
            cid = s.next_id("categories")
            s.categories[cid] = dict(record, category_id=cid)
            return cid

    def update_category(self, category_id, record):
        s = self.store
        with s.lock:
            other = s.category_by_slug(record["slug"])
            if other and other["category_id"] != category_id:
                raise DuplicateKey("That slug is already in use.")
            s.categories[category_id].update(record)

    # ---------- sellers ----------
    def list_sellers(self):
        return [dict(x) for x in self.store.sellers.values()]

    def create_seller(self, record):
        s = self.store
        with s.lock:
            seller_id = f"SEL-{s.next_id('sellers'):04d}"
            s.sellers[seller_id] = dict(record, seller_id=seller_id, _tops=[])
            return seller_id

    def set_seller_status(self, seller_id, status):
        if seller_id in self.store.sellers:
            self.store.sellers[seller_id]["status"] = status

    # ---------- reviews ----------
    def reviews_for(self, product_id, limit=5):
        rows = sorted(self.store.reviews_by_product.get(product_id, []),
                      key=lambda r: r["created_at"], reverse=True)
        return [dict(r) for r in rows[:limit]]

    def rating_counts(self, product_id):
        counts = Counter(r["rating"] for r in self.store.reviews_by_product.get(product_id, []))
        return {stars: counts.get(stars, 0) for stars in range(1, 6)}

    def rating_stats(self):
        stats = {}
        for pid, rows in self.store.reviews_by_product.items():
            if rows:
                stats[pid] = (sum(r["rating"] for r in rows) / len(rows), len(rows))
        return stats

    def has_reviewed(self, buyer_id, product_id):
        return any(r["buyer_id"] == buyer_id for r in self.store.reviews_by_product.get(product_id, []))

    def add_review(self, record):
        s = self.store
        with s.lock:
            if self.has_reviewed(record["buyer_id"], record["product_id"]):
                raise DuplicateKey("You already reviewed this product.")
            review_id = s.next_id("reviews")
            review = dict(record, review_id=review_id)
            s.reviews[review_id] = review
            s.reviews_by_product[record["product_id"]].append(review)
            return review_id

    # ---------- analytics ----------
    def record_sales(self, rows):
        s = self.store
        with s.lock:
            for row in rows:
                s.analytics.append(dict(row, sale_id=s.next_id("analytics")))

    def units_by_day(self, seller_id, since):
        out = Counter()
        start = since.isoformat()
        for sale in self.store.analytics:
            if sale["seller_id"] == seller_id and sale["sale_date"] >= start:
                out[sale["sale_date"]] += sale["quantity_sold"]
        return dict(out)

    def top_products(self, seller_id, since, limit=5):
        out = Counter()
        start = since.isoformat()
        for sale in self.store.analytics:
            if sale["seller_id"] == seller_id and sale["sale_date"] >= start:
                out[sale["product_id"]] += sale["quantity_sold"]
        return out.most_common(limit)

    def seller_history(self, seller_id, since, limit=5):
        start = since.isoformat()
        per = {}
        for sale in self.store.analytics:
            if sale["seller_id"] == seller_id and sale["sale_date"] >= start:
                row = per.setdefault(sale["product_id"], {"product_id": sale["product_id"], "units": 0, "days": set()})
                row["units"] += sale["quantity_sold"]
                row["days"].add(sale["sale_date"])
        rows = sorted(per.values(), key=lambda r: (-r["units"], r["product_id"]))[:limit]
        return [{"product_id": r["product_id"], "units": r["units"], "days_with_sales": len(r["days"]),
                 "first_sale": min(r["days"]), "last_sale": max(r["days"])} for r in rows]

    # ---------- alerts ----------
    def open_alert(self, product_id):
        for alert in self.store.alerts.values():
            if alert["product_id"] == product_id and alert["alert_status"] == "Open":
                return dict(alert)
        return None

    def insert_alert(self, record):
        s = self.store
        with s.lock:
            alert_id = s.next_id("alerts")
            s.alerts[alert_id] = dict(record, alert_id=alert_id)
            return alert_id

    def update_alert(self, alert_id, fields):
        if alert_id in self.store.alerts:
            self.store.alerts[alert_id].update(fields)

    def alerts_for_seller(self, seller_id, limit=6):
        rows = [dict(a) for a in self.store.alerts.values() if a.get("seller_id") == seller_id]
        rows.sort(key=lambda a: (a["alert_status"] != "Open", a["current_stock"]))
        return rows[:limit]

    def count_open_alerts(self, seller_id):
        return sum(1 for a in self.store.alerts.values()
                   if a.get("seller_id") == seller_id and a["alert_status"] == "Open")


class MemoryQuickRepository(QuickRepositoryInterface):
    is_memory = True

    def __init__(self, store):
        self.store = store

    def get_cart(self, user_id):
        return copy.deepcopy(self.store.carts.get(user_id, {}))

    def save_cart(self, user_id, lines):
        self.store.carts[user_id] = copy.deepcopy(lines)

    def get_favorites(self, user_id):
        return dict(self.store.favorites.get(user_id, {}))

    def save_favorites(self, user_id, favorites):
        self.store.favorites[user_id] = dict(favorites)
