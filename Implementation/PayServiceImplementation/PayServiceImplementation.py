"""Orders, payments, shipping and invoices (MySQL tables of the diagram).

place_order() follows the stored procedure "register order with stock
discount": everything is validated first, then all tables are written while
holding the store lock, so either the whole order exists or nothing does.
"""
from collections import defaultdict

from Implementation.DataSource.Inventory import refresh_alert
from Interfaces.PayServiceInterface.PayDTO import (ALLOWED_TRANSITIONS, InvoiceDTO,
                                                   OrderDTO, OrderItemDTO, PaymentDTO,
                                                   ShippingDTO, StatusChangeDTO)
from Interfaces.PayServiceInterface.PayServiceInterface import PayServiceInterface
from Interfaces.Validators import ValidationError, split_iva


class PayServiceImplementation(PayServiceInterface):
    def __init__(self, store, cart):
        self.store = store
        self.cart = cart            # CartServiceImplementation

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

            # Totals are recalculated here from current prices (never from the form).
            subtotal, tax, total = split_iva(cart.gross)
            now = store.now()
            order_id = store.next_id("orders", start=10001)
            store.orders[order_id] = {"order_id": order_id, "buyer_id": user_id,
                                      "created_at": now.isoformat(), "status": "Pending",
                                      "subtotal": subtotal, "tax_amount": tax, "total_amount": total}
            for line in cart.lines:
                p = store.products[line.product.product_id]
                store.order_items[order_id].append({
                    "item_id": store.next_id("order_items"), "order_id": order_id,
                    "product_id": p["product_id"], "quantity": line.qty, "unit_price": p["price"],
                    # Recommended new column for clothing: the chosen variant.
                    "size": line.size, "color": line.color,
                })
                p["stock"] -= line.qty
                refresh_alert(store, p)
            self._history(order_id, None, "Pending")
            store.shipping[order_id] = {"address_id": store.next_id("shipping"), "order_id": order_id,
                                        "street": form.street, "city": form.city,
                                        "state": form.state, "zip_code": form.zip_code}
            store.payments[order_id] = {"payment_id": store.next_id("payments"), "order_id": order_id,
                                        "payment_method": form.method, "card_last4": form.card_last4,
                                        "status": "Awaiting payment"}
            if form.method == "card":
                # Card payments are approved by the provider synchronously.
                self._apply(order_id, "Paid")
            self.cart.clear(user_id)
        return order_id

    # ---------- status ----------
    def change_status(self, order_id, new_status):
        with self.store.lock:
            order = self.store.orders.get(order_id)
            if not order:
                raise ValidationError("Order not found.")
            if new_status not in ALLOWED_TRANSITIONS.get(order["status"], ()):
                raise ValidationError(f"An order that is {order['status']} cannot change to {new_status}.")
            self._apply(order_id, new_status)

    def _history(self, order_id, old, new):
        self.store.status_history[order_id].append({
            "history_id": self.store.next_id("status_history"), "order_id": order_id,
            "old_status": old, "new_status": new, "changed_at": self.store.now().isoformat()})

    def _apply(self, order_id, new_status):
        store = self.store
        order = store.orders[order_id]
        old = order["status"]
        order["status"] = new_status
        self._history(order_id, old, new_status)
        payment = store.payments.get(order_id)
        if new_status == "Paid":
            if payment:
                payment["status"] = "Approved"
            invoice_id = store.next_id("invoices", start=20001)
            store.invoices[order_id] = {"invoice_id": invoice_id, "order_id": order_id,
                                        # In AWS this would be the S3 URL of the generated PDF.
                                        "pdf_receipt_url": f"/orders/{order_id}/invoice",
                                        "issued_at": store.now().isoformat()}
            for item in store.order_items[order_id]:
                p = store.products.get(item["product_id"], {})
                store.analytics.append({
                    "sale_id": store.next_id("analytics"), "product_id": item["product_id"],
                    "category_id": p.get("category_id"), "seller_id": p.get("seller_id"),
                    "quantity_sold": item["quantity"], "sale_date": store.now().date().isoformat()})
        elif new_status == "Cancelled":
            if payment:
                payment["status"] = "Refunded" if old == "Paid" else "Cancelled"
            for item in store.order_items[order_id]:     # give the stock back
                p = store.products.get(item["product_id"])
                if p:
                    p["stock"] += item["quantity"]
                    refresh_alert(store, p)

    # ---------- queries ----------
    def list_orders(self, user_id, status=None, q=""):
        rows = [o for o in self.store.orders.values() if o["buyer_id"] == user_id]
        return self._filter(rows, status, q)

    def all_orders(self, status=None, q=""):
        return self._filter(list(self.store.orders.values()), status, q)

    def _filter(self, rows, status, q):
        if status:
            rows = [o for o in rows if o["status"] == status]
        digits = "".join(c for c in (q or "") if c.isdigit())
        if q and digits:
            rows = [o for o in rows if digits in str(o["order_id"])]
        elif q:
            rows = []
        rows.sort(key=lambda o: o["created_at"], reverse=True)
        return [self._to_dto(o) for o in rows]

    def get_order(self, order_id, user_id=None, is_admin=False):
        order = self.store.orders.get(order_id)
        # Ownership check: a buyer can only open their own orders (prevents IDOR).
        if not order or (not is_admin and order["buyer_id"] != user_id):
            return None
        return self._to_dto(order)

    def _to_dto(self, o):
        store = self.store
        oid = o["order_id"]
        items = []
        for i in store.order_items[oid]:
            p = store.products.get(i["product_id"], {})
            items.append(OrderItemDTO(i["item_id"], i["product_id"], p.get("name", "Product removed"),
                                      i["quantity"], i["unit_price"], i.get("size", ""), i.get("color", ""),
                                      p.get("color_key", "gray"), p.get("kind", "tshirt")))
        history = [StatusChangeDTO(h["history_id"], h["old_status"], h["new_status"], h["changed_at"])
                   for h in store.status_history[oid]]
        pay = store.payments.get(oid)
        ship = store.shipping.get(oid)
        inv = store.invoices.get(oid)
        buyer = store.users.get(o["buyer_id"], {})
        return OrderDTO(
            order_id=oid, buyer_id=o["buyer_id"], buyer_name=buyer.get("name", "—"),
            created_at=o["created_at"], status=o["status"], subtotal=o["subtotal"],
            tax_amount=o["tax_amount"], total_amount=o["total_amount"], items=items, history=history,
            payment=PaymentDTO(pay["payment_id"], pay["payment_method"], pay["status"], pay["card_last4"]) if pay else None,
            shipping=ShippingDTO(ship["address_id"], ship["street"], ship["city"], ship["state"], ship["zip_code"]) if ship else None,
            invoice=InvoiceDTO(inv["invoice_id"], inv["pdf_receipt_url"], inv["issued_at"]) if inv else None,
        )
