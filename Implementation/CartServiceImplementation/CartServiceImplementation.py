"""Shopping cart stored per user (DynamoDB item CART#<user>).

Prices are NEVER stored in the cart: they are read from the product when the
cart is shown and again at checkout, so a tampered form cannot change them.
"""
import hashlib

from Implementation.DataSource.Mappers import is_public, product_to_dto
from Interfaces.CartServiceInterface.CartDTO import (MAX_LINES, MAX_QTY_PER_LINE,
                                                     CartDTO, CartLineDTO)
from Interfaces.CartServiceInterface.CartServiceInterface import CartServiceInterface
from Interfaces.Validators import ValidationError


def line_id_for(product_id, size, color):
    return hashlib.sha1(f"{product_id}|{size}|{color}".encode()).hexdigest()[:12]


class CartServiceImplementation(CartServiceInterface):
    def __init__(self, store):
        self.store = store

    def _lines(self, user_id):
        return self.store.carts.setdefault(user_id, {})

    def get_cart(self, user_id):
        lines, notices = [], []
        with self.store.lock:
            raw = self._lines(user_id)
            for line_id, line in list(raw.items()):
                p = self.store.products.get(line["product_id"])
                if not p or not is_public(self.store, p) or p["stock"] <= 0:
                    name = p["name"] if p else "A product"
                    notices.append(f"{name} is no longer available and was removed from your cart.")
                    del raw[line_id]
                    continue
                if line["qty"] > p["stock"]:
                    line["qty"] = p["stock"]
                    notices.append(f"Only {p['stock']} left of {p['name']}: quantity updated.")
                lines.append(CartLineDTO(line_id=line_id, product=product_to_dto(self.store, p),
                                         size=line["size"], color=line["color"], qty=line["qty"]))
        return CartDTO(lines=lines, notices=notices)

    def add(self, user_id, form):
        p = self.store.products.get(form.product_id)
        if not p or not is_public(self.store, p):
            raise ValidationError("This product is not available.")
        if form.size not in p["sizes"]:
            raise ValidationError("Choose one of the available sizes.")
        if form.color not in p["colors"]:
            raise ValidationError("Choose one of the available colors.")
        with self.store.lock:
            lines = self._lines(user_id)
            line_id = line_id_for(form.product_id, form.size, form.color)
            current = lines.get(line_id, {}).get("qty", 0)
            in_cart_same_product = sum(l["qty"] for l in lines.values() if l["product_id"] == form.product_id)
            if p["stock"] <= 0:
                raise ValidationError("This product is out of stock.")
            if in_cart_same_product + form.qty > p["stock"]:
                raise ValidationError(f"Only {p['stock']} available.")
            if current + form.qty > MAX_QTY_PER_LINE:
                raise ValidationError(f"You can add up to {MAX_QTY_PER_LINE} units per size and color.")
            if line_id not in lines and len(lines) >= MAX_LINES:
                raise ValidationError("Your cart is full. Remove something first.")
            lines[line_id] = {"product_id": form.product_id, "size": form.size,
                              "color": form.color, "qty": current + form.qty}
        return line_id

    def update(self, user_id, line_id, qty):
        with self.store.lock:
            lines = self._lines(user_id)
            line = lines.get(line_id)
            if not line:
                raise ValidationError("That item is no longer in your cart.")
            if qty <= 0:
                del lines[line_id]
                return
            p = self.store.products.get(line["product_id"])
            others = sum(l["qty"] for k, l in lines.items()
                         if l["product_id"] == line["product_id"] and k != line_id)
            limit = min(MAX_QTY_PER_LINE, (p["stock"] if p else 0) - others)
            if qty > limit:
                raise ValidationError(f"Only {max(limit, 0)} available for this item.")
            line["qty"] = qty

    def remove(self, user_id, line_id):
        with self.store.lock:
            self._lines(user_id).pop(line_id, None)

    def count(self, user_id):
        if not user_id:
            return 0
        return sum(line["qty"] for line in self.store.carts.get(user_id, {}).values())

    def clear(self, user_id):
        with self.store.lock:
            self.store.carts[user_id] = {}
