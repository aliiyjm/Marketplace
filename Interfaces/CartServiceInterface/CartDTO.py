"""CartDTO: shopping cart (stored in DynamoDB, one item per user)."""
from dataclasses import dataclass, field
from decimal import Decimal

from Interfaces.MainServiceInterface.MainDTO import valid_product_id
from Interfaces.Validators import ValidationError, clean_text, parse_int, split_iva

MAX_QTY_PER_LINE = 10
MAX_LINES = 30


@dataclass(frozen=True)
class AddToCartForm:
    product_id: str
    size: str
    color: str
    qty: int

    @classmethod
    def from_form(cls, form):
        product_id = form.get("product_id", "")
        if not valid_product_id(product_id):
            raise ValidationError("Unknown product.")
        size = clean_text(form.get("size"), 20, field="Size")
        color = clean_text(form.get("color"), 30, field="Color")
        if not size:
            raise ValidationError("Choose a size.")
        if not color:
            raise ValidationError("Choose a color.")
        qty = parse_int(form.get("qty", 1), min_value=1, max_value=MAX_QTY_PER_LINE,
                        field="Quantity", strict=True)
        return cls(product_id=product_id, size=size, color=color, qty=qty)


@dataclass(frozen=True)
class CartLineDTO:
    line_id: str
    product: object          # ProductDTO
    size: str
    color: str
    qty: int

    @property
    def unit_price(self):
        return self.product.price

    @property
    def line_total(self):
        return self.product.price * self.qty

    @property
    def max_qty(self):
        return min(MAX_QTY_PER_LINE, self.product.stock)


@dataclass(frozen=True)
class CartDTO:
    lines: list = field(default_factory=list)
    notices: list = field(default_factory=list)   # e.g. "X is no longer available"

    @property
    def count(self):
        return sum(line.qty for line in self.lines)

    @property
    def gross(self):
        return sum((line.line_total for line in self.lines), Decimal("0.00"))

    @property
    def totals(self):
        """(subtotal, tax, total): prices already include 16% IVA."""
        return split_iva(self.gross)

    @property
    def empty(self):
        return not self.lines

