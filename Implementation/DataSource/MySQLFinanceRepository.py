"""Orders in MySQL finance_db (tables of scripts finance_db.sql):
orders, order_items, payments, shipping_addresses, invoices, order_status_history.

The database keeps the conventions written in its script (statuses and
payment methods in UPPERCASE: PENDING, PAID, CARD, TRANSFER, SUCCESS...);
this class translates them to the names the app shows.

With MySQL every write goes through the STORED PROCEDURES of
database/finance_procedures.sql (CALL sp_create_order, sp_change_order_status,
sp_cancel_order) and the buyer history comes from sp_buyer_sales_history.
SQLite (automated tests) has no procedures, so there the same rules run as
plain parameterized SQL in one transaction.
"""
import json

from Implementation.DataSource.SqlConnection import as_datetime, as_iso, as_money
from Interfaces.DataInterfaces.FinanceRepositoryInterface import (FinanceRepositoryInterface,
                                                                  history_summary)
from Interfaces.DataInterfaces.RepositoryErrors import ProcedureError, StaleWrite
from Interfaces.Validators import ValidationError

ORDER_STATUS = {"Pending": "PENDING", "Paid": "PAID", "Shipped": "SHIPPED",
                "Delivered": "DELIVERED", "Cancelled": "CANCELLED"}
PAY_METHOD = {"card": "CARD", "spei": "TRANSFER", "cash": "CASH"}
PAY_STATUS = {"Approved": "SUCCESS", "Awaiting payment": "PENDING", "Cancelled": "CANCELLED",
              "Refunded": "REFUNDED", "Failed": "FAILED"}
MAX_LIST = 200
# Codes a stored procedure can SIGNAL -> message for the page.
PROCEDURE_MESSAGES = {
    "ORDER_NOT_FOUND": "Order not found.",
    "INVALID_TRANSITION": "That status change is not allowed.",
    "CANNOT_CANCEL": "Only pending or paid orders can be cancelled.",
    "USE_SP_CANCEL_ORDER": "Cancellations go through sp_cancel_order.",
    "EMPTY_ORDER": "The order has no items.",
    "INVALID_ORDER_ITEMS": "The order has invalid items.",
    "INVALID_PAYMENT_METHOD": "Choose a valid payment method.",
    "INVALID_CARD_LAST4": "The card number is invalid.",
    "INVALID_CARD_TOKEN": "The card could not be tokenized. Try again.",
    "INVALID_ZIP_CODE": "The ZIP code must have 5 digits.",
    "INVALID_ORDER_ID": "Invalid order number.",
}


def _to_db(mapping, value):
    return mapping.get(value, value)


def _from_db(mapping, value):
    if value is None:
        return None
    reverse = {v: k for k, v in mapping.items()}
    return reverse.get(str(value).upper(), value)


def _procedure_error(error):
    if error.code == "STALE_ORDER":
        return StaleWrite("The order changed meanwhile. Reload the page and try again.")
    return ValidationError(PROCEDURE_MESSAGES.get(error.code, "The operation is not allowed."))


def _lines_json(items):
    return json.dumps([{"product_id": i["product_id"], "quantity": int(i["quantity"]),
                        "unit_price": str(as_money(i["unit_price"])), "size": i.get("size"),
                        "color": i.get("color")} for i in items])


class MySQLFinanceRepository(FinanceRepositoryInterface):
    def __init__(self, db, use_procedures=None):
        self.db = db               # SqlDatabase (mysql, or sqlite in the tests)
        self.use_procedures = db.dialect == "mysql" if use_procedures is None else use_procedures

    # ---------- writes ----------
    def next_order_id(self):
        rows = self.db.query("SELECT order_id FROM orders")
        numbers = [int(r["order_id"]) for r in rows if str(r["order_id"]).isdigit()]
        return max(numbers + [10000]) + 1

    def create_order(self, order, items, shipping, payment, history):
        oid = str(order["order_id"])
        if self.use_procedures:
            try:
                with self.db.transaction() as cur:
                    cur.call("CALL sp_create_order(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                             (oid, order["buyer_id"], as_datetime(order["created_at"]), _lines_json(items),
                              shipping["street"], shipping["city"], shipping["state"], shipping["zip_code"],
                              _to_db(PAY_METHOD, payment["payment_method"]), payment.get("card_last4") or None,
                              payment.get("card_token") or None))
            except ProcedureError as e:
                raise _procedure_error(e) from None
            return
        with self.db.transaction() as cur:
            cur.execute("INSERT INTO orders (order_id, buyer_id, created_at, status, subtotal, tax_amount, "
                        "total_amount) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (oid, order["buyer_id"], as_datetime(order["created_at"]),
                         _to_db(ORDER_STATUS, order["status"]), order["subtotal"], order["tax_amount"],
                         order["total_amount"]))
            for item in items:
                cur.execute("INSERT INTO order_items (order_id, product_id, quantity, unit_price, size, color) "
                            "VALUES (%s, %s, %s, %s, %s, %s)",
                            (oid, item["product_id"], item["quantity"], item["unit_price"],
                             item.get("size"), item.get("color")))
            cur.execute("INSERT INTO shipping_addresses (order_id, street, city, state, zip_code) "
                        "VALUES (%s, %s, %s, %s, %s)",
                        (oid, shipping["street"], shipping["city"], shipping["state"], shipping["zip_code"]))
            cur.execute("INSERT INTO payments (payment_id, order_id, payment_method, card_last4, card_token, "
                        "status, processed_at) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (payment["payment_id"], oid, _to_db(PAY_METHOD, payment["payment_method"]),
                         payment.get("card_last4") or None, payment.get("card_token") or None,
                         _to_db(PAY_STATUS, payment["status"]), as_datetime(payment.get("processed_at"))))
            cur.execute("INSERT INTO order_status_history (order_id, old_status, new_status, changed_at) "
                        "VALUES (%s, %s, %s, %s)",
                        (oid, _to_db(ORDER_STATUS, history["old_status"]) if history["old_status"] else None,
                         _to_db(ORDER_STATUS, history["new_status"]), as_datetime(history["changed_at"])))

    def update_status(self, order_id, old_status, new_status, changed_at, payment_status=None, invoice=None):
        oid = str(order_id)
        if self.use_procedures:
            try:
                with self.db.transaction() as cur:
                    cur.call("CALL sp_change_order_status(%s, %s, %s, %s, %s, %s)",
                             (oid, _to_db(ORDER_STATUS, old_status), _to_db(ORDER_STATUS, new_status),
                              as_datetime(changed_at), str(invoice["invoice_id"]) if invoice else None,
                              invoice["pdf_receipt_url"] if invoice else None))
            except ProcedureError as e:
                raise _procedure_error(e) from None
            return
        with self.db.transaction() as cur:
            cur.execute("UPDATE orders SET status = %s WHERE order_id = %s AND status = %s",
                        (_to_db(ORDER_STATUS, new_status), oid, _to_db(ORDER_STATUS, old_status)))
            if cur.rowcount != 1:      # someone changed it first: do not overwrite
                raise StaleWrite("The order changed meanwhile. Reload the page and try again.")
            cur.execute("INSERT INTO order_status_history (order_id, old_status, new_status, changed_at) "
                        "VALUES (%s, %s, %s, %s)",
                        (oid, _to_db(ORDER_STATUS, old_status), _to_db(ORDER_STATUS, new_status),
                         as_datetime(changed_at)))
            if payment_status:
                cur.execute("UPDATE payments SET status = %s, processed_at = %s WHERE order_id = %s",
                            (_to_db(PAY_STATUS, payment_status), as_datetime(changed_at), oid))
            if invoice:
                cur.execute("INSERT INTO invoices (invoice_id, order_id, pdf_receipt_url, issued_at) "
                            "VALUES (%s, %s, %s, %s)",
                            (str(invoice["invoice_id"]), oid, invoice["pdf_receipt_url"],
                             as_datetime(invoice["issued_at"])))

    def cancel_order(self, order_id, old_status, changed_at):
        oid = str(order_id)
        if self.use_procedures:
            try:
                with self.db.transaction() as cur:
                    rows = cur.call("CALL sp_cancel_order(%s, %s, %s)",
                                    (oid, _to_db(ORDER_STATUS, old_status), as_datetime(changed_at)))
            except ProcedureError as e:
                raise _procedure_error(e) from None
            return {r["product_id"]: int(r["quantity"]) for r in rows}
        if old_status not in ("Pending", "Paid"):
            raise ValidationError(PROCEDURE_MESSAGES["CANNOT_CANCEL"])
        self.update_status(oid, old_status, "Cancelled", changed_at,
                           "Refunded" if old_status == "Paid" else "Cancelled")
        rows = self.db.query("SELECT product_id, SUM(quantity) AS quantity FROM order_items "
                             "WHERE order_id = %s GROUP BY product_id ORDER BY product_id", (oid,))
        return {r["product_id"]: int(r["quantity"]) for r in rows}

    def buyer_history(self, buyer_id):
        if self.use_procedures:
            with self.db.transaction() as cur:
                rows = cur.call("CALL sp_buyer_sales_history(%s)", (buyer_id,))
        else:
            rows = self.db.query(
                "SELECT o.order_id, o.created_at, o.status, o.total_amount, COUNT(i.item_id) AS line_count, "
                "COALESCE(SUM(i.quantity), 0) AS units FROM orders o "
                "LEFT JOIN order_items i ON i.order_id = o.order_id WHERE o.buyer_id = %s "
                "GROUP BY o.order_id, o.created_at, o.status, o.total_amount "
                "ORDER BY o.created_at DESC, o.order_id DESC", (buyer_id,))
        orders = [{"order_id": int(r["order_id"]) if str(r["order_id"]).isdigit() else r["order_id"],
                   "created_at": as_iso(r["created_at"]), "status": _from_db(ORDER_STATUS, r["status"]),
                   "total_amount": as_money(r["total_amount"]), "line_count": int(r["line_count"]),
                   "units": int(r["units"])} for r in rows]
        return history_summary(orders)

    # ---------- reads ----------
    def get_order(self, order_id):
        with self.db.transaction() as cur:
            row = cur.execute("SELECT * FROM orders WHERE order_id = %s", (str(order_id),)).fetchone()
            return self._full_many(cur, [row])[0] if row else None

    def list_orders(self, buyer_id=None, status=None, order_id_contains=None):
        sql, params = "SELECT * FROM orders WHERE 1 = 1", []
        if buyer_id is not None:
            sql += " AND buyer_id = %s"
            params.append(buyer_id)
        if status:
            sql += " AND status = %s"
            params.append(_to_db(ORDER_STATUS, status))
        if order_id_contains:
            sql += " AND order_id LIKE %s"
            params.append(f"%{order_id_contains}%")
        sql += " ORDER BY created_at DESC LIMIT %s"
        params.append(MAX_LIST)
        with self.db.transaction() as cur:
            return self._full_many(cur, cur.execute(sql, params).fetchall())

    def has_bought(self, buyer_id, product_id):
        row = self.db.query_one(
            "SELECT 1 AS ok FROM orders o JOIN order_items i ON i.order_id = o.order_id "
            "WHERE o.buyer_id = %s AND i.product_id = %s AND o.status <> %s LIMIT 1",
            (buyer_id, product_id, ORDER_STATUS["Cancelled"]))
        return row is not None

    # ---------- helpers ----------
    def _full_many(self, cur, orders):
        """Load the child rows of many orders with one query per table (no N+1)."""
        if not orders:
            return []
        ids = [str(o["order_id"]) for o in orders]
        marks = ", ".join(["%s"] * len(ids))

        def grouped(sql):
            out = {}
            for row in cur.execute(sql.format(marks=marks), ids).fetchall():
                out.setdefault(str(row["order_id"]), []).append(row)
            return out

        items = grouped("SELECT * FROM order_items WHERE order_id IN ({marks}) ORDER BY item_id")
        history = grouped("SELECT * FROM order_status_history WHERE order_id IN ({marks}) ORDER BY history_id")
        payments = grouped("SELECT * FROM payments WHERE order_id IN ({marks})")
        shipping = grouped("SELECT * FROM shipping_addresses WHERE order_id IN ({marks}) ORDER BY address_id")
        invoices = grouped("SELECT * FROM invoices WHERE order_id IN ({marks})")
        result = []
        for o in orders:
            oid = str(o["order_id"])
            pay = (payments.get(oid) or [None])[0]
            ship = (shipping.get(oid) or [None])[0]
            inv = (invoices.get(oid) or [None])[0]
            result.append({
                "order": {"order_id": int(oid), "buyer_id": o["buyer_id"], "created_at": as_iso(o["created_at"]),
                          "status": _from_db(ORDER_STATUS, o["status"]), "subtotal": as_money(o["subtotal"]),
                          "tax_amount": as_money(o["tax_amount"]), "total_amount": as_money(o["total_amount"])},
                "items": [{"item_id": int(i["item_id"]), "order_id": int(oid), "product_id": i["product_id"],
                           "quantity": int(i["quantity"]), "unit_price": as_money(i["unit_price"]),
                           "size": i.get("size") or "", "color": i.get("color") or ""}
                          for i in items.get(oid, [])],
                "history": [{"history_id": int(h["history_id"]), "order_id": int(oid),
                             "old_status": _from_db(ORDER_STATUS, h["old_status"]),
                             "new_status": _from_db(ORDER_STATUS, h["new_status"]),
                             "changed_at": as_iso(h["changed_at"])} for h in history.get(oid, [])],
                "payment": {"payment_id": pay["payment_id"], "order_id": int(oid),
                            "payment_method": _from_db(PAY_METHOD, pay["payment_method"]),
                            "card_last4": pay.get("card_last4") or "", "card_token": pay.get("card_token") or "",
                            "status": _from_db(PAY_STATUS, pay["status"]),
                            "processed_at": as_iso(pay.get("processed_at"))} if pay else None,
                "shipping": {"address_id": int(ship["address_id"]), "order_id": int(oid), "street": ship["street"],
                             "city": ship["city"], "state": ship["state"], "zip_code": ship["zip_code"]}
                if ship else None,
                "invoice": {"invoice_id": int(inv["invoice_id"]) if str(inv["invoice_id"]).isdigit()
                            else inv["invoice_id"], "order_id": int(oid),
                            "pdf_receipt_url": inv["pdf_receipt_url"], "issued_at": as_iso(inv["issued_at"])}
                if inv else None,
            })
        return result
