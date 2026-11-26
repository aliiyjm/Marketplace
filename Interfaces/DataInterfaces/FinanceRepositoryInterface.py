"""Contract for the transactional data (MySQL finance_db):
orders, order_items, payments, shipping_addresses, invoices, order_status_history.

Records are plain dicts with the same keys as the table columns. The
implementation translates to/from the database conventions (for example
statuses stored in uppercase).

A full order is returned as:
    {"order": {...}, "items": [...], "history": [...],
     "payment": {...} | None, "shipping": {...} | None, "invoice": {...} | None}
"""
from abc import ABC, abstractmethod
from decimal import Decimal


def history_summary(rows):
    """Totals of a buyer history (same as the window functions of
    sp_buyer_sales_history). Cancelled orders do not count as spent."""
    active = [r for r in rows if r["status"] != "Cancelled"]
    return {"orders": rows, "orders_count": len(rows), "active_orders": len(active),
            "total_spent": sum((Decimal(str(r["total_amount"])) for r in active), Decimal("0.00")),
            "units_bought": sum(int(r["units"]) for r in active)}


class FinanceRepositoryInterface(ABC):
    is_memory = False

    @abstractmethod
    def next_order_id(self):
        """A new order number (int, >= 10001)."""

    @abstractmethod
    def create_order(self, order, items, shipping, payment, history):
        """Write the order and its rows in ONE transaction (stored procedure
        sp_create_order in MySQL). Raises DuplicateKey if order_id is taken."""

    @abstractmethod
    def update_status(self, order_id, old_status, new_status, changed_at,
                      payment_status=None, invoice=None):
        """In ONE transaction (stored procedure sp_change_order_status in MySQL):
        change the status only if it is still old_status (else StaleWrite), add the
        history row, update the payment, add the invoice. Not for cancellations."""

    @abstractmethod
    def cancel_order(self, order_id, old_status, changed_at):
        """In ONE transaction (stored procedure sp_cancel_order in MySQL): cancel
        only if the order is still old_status (else StaleWrite), refund or cancel
        the payment and add the history row. Returns {product_id: quantity} to
        put back in stock."""

    @abstractmethod
    def buyer_history(self, buyer_id):
        """Consolidated history (stored procedure sp_buyer_sales_history):
        {"orders": [{"order_id","created_at","status","total_amount","line_count","units"}],
         "orders_count", "active_orders", "total_spent", "units_bought"}."""

    @abstractmethod
    def get_order(self, order_id):
        """Full order or None."""

    @abstractmethod
    def list_orders(self, buyer_id=None, status=None, order_id_contains=None):
        """Full orders, newest first."""

    @abstractmethod
    def has_bought(self, buyer_id, product_id):
        """True if the buyer has a non-cancelled order with that product."""
