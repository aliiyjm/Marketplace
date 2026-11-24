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


class FinanceRepositoryInterface(ABC):
    is_memory = False

    @abstractmethod
    def next_order_id(self):
        """A new order number (int, >= 10001)."""

    @abstractmethod
    def create_order(self, order, items, shipping, payment, history):
        """Write the order and its rows in ONE transaction.
        Raises DuplicateKey if order_id is taken (caller retries)."""

    @abstractmethod
    def update_status(self, order_id, old_status, new_status, changed_at,
                      payment_status=None, invoice=None):
        """In ONE transaction: change the status only if it is still old_status
        (else StaleWrite), add the history row, update the payment, add the invoice."""

    @abstractmethod
    def get_order(self, order_id):
        """Full order or None."""

    @abstractmethod
    def list_orders(self, buyer_id=None, status=None, order_id_contains=None):
        """Full orders, newest first."""

    @abstractmethod
    def has_bought(self, buyer_id, product_id):
        """True if the buyer has a non-cancelled order with that product."""
