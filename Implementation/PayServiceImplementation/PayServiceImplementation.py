"""Orders, payments, shipping and invoices (MySQL finance_db through store.finance).

The three atomic operations the project asks for:

1. place_order  = create the order AND discount the inventory
   a) ATOMIC TRANSACTION in DynamoDB (TransactWriteItems, Lambda route
      /productos/reservar): the stock of EVERY product of the cart is
      discounted at once, or none is. The reservation is keyed by the order
      number, so two checkouts can never take the same number.
   b) STORED PROCEDURE sp_create_order (MySQL): order, items, shipping,
      payment and history in one transaction; totals and IVA computed there.
   If (b) fails, (a) is undone with the release transaction.

2. cancel (change_status -> Cancelled) = cancel AND restock
   a) STORED PROCEDURE sp_cancel_order: locks the order row, checks the
      status, refunds/cancels the payment, writes history, returns the units.
   b) ATOMIC TRANSACTION in DynamoDB (/productos/liberar): gives every unit
      back; a condition on the reservation makes it happen only once.

3. sales history = sp_buyer_sales_history (MySQL, per buyer) and
   fn_seller_sales_history (PostgreSQL, per seller).
"""
import logging
from collections import defaultdict

from Implementation.DataSource.Inventory import refresh_alert
from Implementation.DataSource.ProductRepositories import adjust_many
from Interfaces.DataInterfaces.RepositoryErrors import DuplicateKey
from Interfaces.ProductServiceInterface.ProductServiceInterface import (OutOfStock, ProductStoreError,
                                                                        ReservationExists)
from Interfaces.PayServiceInterface.PayDTO import (ALLOWED_TRANSITIONS, BuyerHistoryDTO, InvoiceDTO,
                                                   OrderDTO, OrderItemDTO, PaymentDTO,
                                                   ShippingDTO, StatusChangeDTO)
from Interfaces.PayServiceInterface.PayServiceInterface import PayServiceInterface
from Interfaces.Validators import ValidationError, split_iva

log = logging.getLogger("orders")
MAX_NUMBER_TRIES = 50     # order numbers skipped because another checkout reserved them


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
            order_id = self._reserve_and_write(user_id, form, cart, dict(needed))
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

    def _set_stock(self, stocks):
        for product_id, stock in (stocks or {}).items():
            if product_id in self.store.products:
                self.store.products[product_id]["stock"] = stock

    def _reserve_and_write(self, user_id, form, cart, needed):
        """Atomic stock reservation (DynamoDB) + sp_create_order (MySQL).
        A number already reserved or written by someone else -> try the next one."""
        store = self.store
        for _ in range(5):
            order_id = self.repo.next_order_id()
            for _ in range(MAX_NUMBER_TRIES):
                try:
                    self._set_stock(store.product_repo.reserve_stock(order_id, needed))
                    break
                except ReservationExists:          # another checkout holds this number
                    order_id += 1
                except OutOfStock:
                    self._refresh_stock(needed)        # show the real stock on the next page
                    raise
            else:
                raise ValidationError("Could not register the order. Please try again.")
            try:
                self._write_order(order_id, user_id, form, cart)
                return order_id
            except DuplicateKey:
                self._release(order_id)
                continue
            except Exception:
                self._release(order_id)            # nothing was saved: give the stock back
                raise
        raise ValidationError("Could not register the order. Please try again.")

    def _refresh_stock(self, product_ids):
        for product_id in product_ids:
            try:
                fresh = self.store.product_repo.get(product_id)
            except ProductStoreError:
                continue
            if fresh is not None and fresh is not self.store.products.get(product_id):
                self.store.products[product_id]["stock"] = fresh["stock"]

    def _release(self, order_id):
        try:
            self._set_stock(self.store.product_repo.release_stock(order_id))
        except ProductStoreError:
            log.error("Stock of order %s is still reserved: release it with "
                      "POST /productos/liberar {\"order_id\": \"%s\"}", order_id, order_id)

    def _write_order(self, order_id, user_id, form, cart):
        """Totals are recalculated here from current prices (never from the form);
        with MySQL the stored procedure computes them again from the lines."""
        subtotal, tax, total = split_iva(cart.gross)
        now = self.store.now().isoformat()
        items = [{"product_id": line.product.product_id, "quantity": line.qty,
                  "unit_price": self.store.products[line.product.product_id]["price"],
                  "size": line.size, "color": line.color} for line in cart.lines]
        shipping = {"street": form.street, "city": form.city, "state": form.state, "zip_code": form.zip_code}
        order = {"order_id": order_id, "buyer_id": user_id, "created_at": now, "status": "Pending",
                 "subtotal": subtotal, "tax_amount": tax, "total_amount": total}
        payment = {"payment_id": f"PAY-{order_id}", "payment_method": form.method,
                   "card_last4": form.card_last4, "card_token": form.card_token,
                   "status": "Awaiting payment", "processed_at": now}
        history = {"old_status": None, "new_status": "Pending", "changed_at": now}
        self.repo.create_order(order, items, shipping, payment, history)

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

    def _cancel(self, order_id, old, now):
        """sp_cancel_order first (it locks the order, so only one cancel wins),
        then the atomic, once-only stock release in DynamoDB."""
        store = self.store
        returned = self.repo.cancel_order(order_id, old, now)
        stocks = None
        for attempt in range(3):
            try:
                stocks = store.product_repo.release_stock(order_id)
                break
            except ProductStoreError:
                if attempt == 2:
                    log.error("Order %s was cancelled but its stock could not be returned yet: "
                              "release it with POST /productos/liberar", order_id)
                    raise ValidationError("The order was cancelled, but the stock could not be returned. "
                                          "Ask an administrator to release it.") from None
        if stocks is None:
            # Orders created before stock reservations existed: give the units back one by one.
            adjust_many(store, [(pid, qty) for pid, qty in returned.items() if pid in store.products])
        else:
            self._set_stock(stocks)
        for product_id in returned:
            if product_id in store.products:
                refresh_alert(store, store.products[product_id])

    def _apply(self, order_id, new_status, full=None):
        store = self.store
        full = full or self.repo.get_order(order_id)
        old = full["order"]["status"]
        now = store.now().isoformat()
        if new_status == "Cancelled":
            self._cancel(order_id, old, now)
            return
        payment_status, invoice = None, None
        if new_status == "Paid":
            payment_status = "Approved"
            # In AWS the URL would point to the PDF in S3; here it is the printable page.
            invoice = {"invoice_id": order_id + 10000, "pdf_receipt_url": f"/orders/{order_id}/invoice",
                       "issued_at": now}
        self.repo.update_status(order_id, old, new_status, now, payment_status, invoice)
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
    def sales_history(self, user_id):
        """Consolidated history of the buyer (stored procedure sp_buyer_sales_history)."""
        h = self.repo.buyer_history(user_id)
        return BuyerHistoryDTO(orders_count=h["orders_count"], active_orders=h["active_orders"],
                               total_spent=h["total_spent"], units_bought=h["units_bought"])

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
                                      p.get("color_key", "gray"), p.get("kind", "tshirt"),
                                      p.get("image_url", "")))
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
