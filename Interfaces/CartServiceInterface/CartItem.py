"""CartItem: a user's cart as ONE item of DynamoDB MarketplaceDB.

    PK = CART#<id_user>    SK = CART    lines = [{line_id, product_id, size, color, qty}]

Prices are not stored: they are always read from the product. Validated in the
Lambda with a whitelist (never trust the caller).
"""
import re
from dataclasses import dataclass

from Interfaces.CartServiceInterface.CartDTO import MAX_LINES, MAX_QTY_PER_LINE
from Interfaces.Validators import (PRODUCT_ID_RE, USER_ID_RE, ValidationError,
                                   clean_text)

CART_PK_PREFIX = "CART#"
CART_SK = "CART"
LINE_ID_RE = re.compile(r"[0-9a-f]{12}")


@dataclass(frozen=True)
class CartItem:
    id_user: str
    lines: tuple
    updated_at: str = ""

    @classmethod
    def from_payload(cls, data):
        if not isinstance(data, dict):
            raise ValidationError("Cart must be a JSON object.")
        id_user = str(data.get("id_user", ""))
        if not USER_ID_RE.fullmatch(id_user):
            raise ValidationError("id_user is invalid.")
        raw = data.get("lines", [])
        if not isinstance(raw, list) or len(raw) > MAX_LINES:
            raise ValidationError(f"lines must be a list of at most {MAX_LINES} items.")
        lines, seen = [], set()
        for line in raw:
            if not isinstance(line, dict):
                raise ValidationError("Every line must be an object.")
            line_id = str(line.get("line_id", ""))
            product_id = str(line.get("product_id", ""))
            qty = line.get("qty")
            if not LINE_ID_RE.fullmatch(line_id) or line_id in seen:
                raise ValidationError("line_id is invalid or repeated.")
            if not PRODUCT_ID_RE.fullmatch(product_id):
                raise ValidationError("product_id is invalid.")
            if not isinstance(qty, int) or isinstance(qty, bool) or not 1 <= qty <= MAX_QTY_PER_LINE:
                raise ValidationError(f"qty must be between 1 and {MAX_QTY_PER_LINE}.")
            seen.add(line_id)
            lines.append({"line_id": line_id, "product_id": product_id,
                          "size": clean_text(line.get("size"), 20, field="size", required=True),
                          "color": clean_text(line.get("color"), 30, field="color", required=True),
                          "qty": qty})
        return cls(id_user=id_user, lines=tuple(lines),
                   updated_at=clean_text(data.get("updated_at", ""), 32, field="updated_at"))

    def to_item(self):
        return {"PK": CART_PK_PREFIX + self.id_user, "SK": CART_SK, "entity": "CART",
                "id_user": self.id_user, "lines": [dict(l) for l in self.lines], "updated_at": self.updated_at}

    @classmethod
    def from_item(cls, item):
        return cls(id_user=item["id_user"], updated_at=item.get("updated_at", ""),
                   lines=tuple(dict(l, qty=int(l["qty"])) for l in item.get("lines", [])))

    def to_payload(self):
        return {"id_user": self.id_user, "lines": [dict(l) for l in self.lines], "updated_at": self.updated_at}
