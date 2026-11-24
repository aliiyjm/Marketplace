"""Orders, payments, shipping and invoices (MySQL finance_db through store.finance).

place_order() follows the stored procedure "register order with stock
discount": everything is validated first, the stock is discounted in the
product database (atomic, conditional; undone if anything fails), and then
the order, its items, shipping, payment and first history row are written in
ONE MySQL transaction. Either the whole order exists or nothing does.
"""
import logging
from collections import defaultdict

from Implementation.DataSource.Inventory import refresh_alert
from Implementation.DataSource.ProductRepositories import adjust_many
from Interfaces.DataInterfaces.RepositoryErrors import DuplicateKey
from Interfaces.PayServiceInterface.PayDTO import (ALLOWED_TRANSITIONS, InvoiceDTO,
                                                   OrderDTO, OrderItemDTO, PaymentDTO,
                                                   ShippingDTO, StatusChangeDTO)
from Interfaces.PayServiceInterface.PayServiceInterface import PayServiceInterface
from Interfaces.Validators import ValidationError, split_iva

log = logging.getLogger("orders")


class PayServiceImplementation(PayServiceInterface):
    def __init__(self, store, cart):
        self.store = store
        self.cart = cart            # CartServiceImplementation

    @property
    def repo(self):
        return self.store.finance

    # ---------- checkout ----------
    def place_order(self, user_id, form):
        store = self.store
        with store.lock:
            cart = self.cart.get_cart(user_id)
            if cart.notices:
                raise ValidationError(cart.notices + ["Please review your cart and try again."])
            if cart.empty:
                raise ValidationError("Your cart is empty.")
            needed = defaultdict(int)
            for line in cart.lines:
                needed[line.product.product_id] += line.qty
            for product_id, qty in needed.items():
                p = store.products[product_id]
                if qty > p["stock"]:
                    raise ValidationError(f"Only {p['stock']} left of {p['name']}.")
            # 1) Discount stock in the product database (it refuses to go below 0).
            adjust_many(store, [(pid, -qty) for pid, qty in needed.items()])
            try:
                order_id = self._write_order(user_id, form, cart)
            except Exception:
                # 2) The order could not be saved: give the stock back.
                adjust_many(store, [(pid, qty) for pid, qty in needed.items()])
                raise
            for product_id in needed:
                refresh_alert(store, store.products[product_id])
            if form.method == "card":
                # Card payments are approved by the provider synchronously.
                try:
                    self._apply(order_id, "Paid")
                except ValidationError:
                    # The order already exists (Pending); an admin can mark it as paid.
                    log.error("Order %s was saved but could not be marked as paid", order_id)
            self.cart.clear(user_id)
        return order_id

    def _write_order(self, user_id, form, cart):
        """Totals are recalculated here from current prices (never from the form)."""
        subtotal, tax, total = split_iva(cart.gross)
        now = self.store.now().isoformat()
        items = [{"product_id": line.product.product_id, "quantity": line.qty,
                  "unit_price": self.store.products[line.product.product_id]["price"],
                  "size": line.size, "color": line.color} for line in cart.lines]
        shipping = {"street": form.street, "city": form.city, "state": form.state, "zip_code": form.zip_code}
        for _ in range(3):      # someone else may take the same order number meanwhile
            order_id = self.repo.next_order_id()
            order = {"order_id": order_id, "buyer_id": user_id, "created_at": now, "status": "Pending",
                     "subtotal": subtotal, "tax_amount": tax, "total_amount": total}
            payment = {"payment_id": f"PAY-{order_id}", "payment_method": form.method,
                       "card_last4": form.card_last4, "status": "Awaiting payment", "processed_at": now}
            history = {"old_status": None, "new_status": "Pending", "changed_at": now}
            try:
                self.repo.create_order(order, items, shipping, payment, history)
                return order_id
            except DuplicateKey:
                continue
        raise ValidationError("Could not register the order. Please try again.")

    # ---------- status ----------
    def change_status(self, order_id, new_status):
        with self.store.lock:
            full = self.repo.get_order(order_id)
            if not full:
                raise ValidationError("Order not found.")
            status = full["order"]["status"]
            if new_status not in ALLOWED_TRANSITIONS.get(status, ()):
                raise ValidationError(f"An order that is {status} cannot change to {new_status}.")
            self._apply(order_id, new_status, full)

    def _apply(self, order_id, new_status, full=None):
        store = self.store
        full = full or self.repo.get_order(order_id)
        old = full["order"]["status"]
        now = store.now().isoformat()
        returned = defaultdict(int)
        if new_status == "Cancelled":
            # Give the stock back BEFORE changing anything, so a database error
            # leaves the order exactly as it was.
            for item in full["items"]:
                if item["product_id"] in store.products:
                    returned[item["product_id"]] += item["quantity"]
            adjust_many(store, list(returned.items()))
        payment_status, invoice = None, None
        if new_status == "Paid":
            payment_status = "Approved"
            # In AWS the URL would point to the PDF in S3; here it is the printable page.
            invoice = {"invoice_id": order_id + 10000, "pdf_receipt_url": f"/orders/{order_id}/invoice",
                       "issued_at": now}
        elif new_status == "Cancelled":
            payment_status = "Refunded" if old == "Paid" else "Cancelled"
        try:
            self.repo.update_status(order_id, old, new_status, now, payment_status, invoice)
        except Exception:
            if returned:   # the status did not change: take the returned stock again
                adjust_many(store, [(pid, -qty) for pid, qty in returned.items()])
            raise
        for product_id in returned:
            refresh_alert(store, store.products[product_id])
        if new_status == "Paid":
            sales = []
            for item in full["items"]:
                p = store.products.get(item["product_id"])
                if p:
                    sales.append({"product_id": item["product_id"], "category_id": p["category_id"],
                                  "seller_id": p["seller_id"], "quantity_sold": item["quantity"],
                                  "sale_date": store.now().date().isoformat()})
            try:
                store.ops.record_sales(sales)     # analytics are secondary: never undo a payment for them
            except ValidationError:
                log.error("Could not record the sales of order %s in analytics", order_id)

    # ---------- queries ----------
    def list_orders(self, user_id, status=None, q=""):
        return self._query(user_id, status, q)

    def all_orders(self, status=None, q=""):
        return self._query(None, status, q)

    def _query(self, buyer_id, status, q):
        digits = "".join(c for c in (q or "") if c.isdigit())
        if q and not digits:
            return []
        return [self._to_dto(full) for full in self.repo.list_orders(buyer_id, status or None, digits or None)]

    def get_order(self, order_id, user_id=None, is_admin=False):
        full = self.repo.get_order(order_id)
        # Ownership check: a buyer can only open their own orders (prevents IDOR).
        if not full or (not is_admin and full["order"]["buyer_id"] != user_id):
            return None
        return self._to_dto(full)

    def _to_dto(self, full):
        store = self.store
        o = full["order"]
        items = []
        for i in full["items"]:
            p = store.products.get(i["product_id"], {})
            items.append(OrderItemDTO(i["item_id"], i["product_id"], p.get("name", "Product removed"),
                                      i["quantity"], i["unit_price"], i.get("size") or "", i.get("color") or "",
                                      p.get("color_key", "gray"), p.get("kind", "tshirt")))
        history = [StatusChangeDTO(h["history_id"], h["old_status"], h["new_status"], h["changed_at"])
                   for h in full["history"]]
        pay, ship, inv = full["payment"], full["shipping"], full["invoice"]
        return OrderDTO(
            order_id=o["order_id"], buyer_id=o["buyer_id"], buyer_name=store.user_name(o["buyer_id"]),
            created_at=o["created_at"], status=o["status"], subtotal=o["subtotal"],
            tax_amount=o["tax_amount"], total_amount=o["total_amount"], items=items, history=history,
            payment=PaymentDTO(pay["payment_id"], pay["payment_method"], pay["status"], pay.get("card_last4") or "")
            if pay else None,
            shipping=ShippingDTO(ship["address_id"], ship["street"], ship["city"], ship["state"], ship["zip_code"])
            if ship else None,
            invoice=InvoiceDTO(inv["invoice_id"], inv["pdf_receipt_url"], inv["issued_at"]) if inv else None,
        )
