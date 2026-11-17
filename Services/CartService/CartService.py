"""CartService: cart operations (add, update, remove, query)."""
from Interfaces.CartServiceInterface.CartDTO import MAX_QTY_PER_LINE, AddToCartForm
from Interfaces.Validators import ValidationError, parse_int


class CartService:
    def __init__(self, impl):
        self.impl = impl          # CartServiceInterface

    def view(self, user_id):
        return self.impl.get_cart(user_id)

    def add(self, user_id, form_data):
        return self.impl.add(user_id, AddToCartForm.from_form(form_data))

    def update(self, user_id, line_id, raw_qty):
        qty = parse_int(raw_qty, min_value=0, max_value=MAX_QTY_PER_LINE, field="Quantity", strict=True)
        if not (isinstance(line_id, str) and len(line_id) == 12 and line_id.isalnum()):
            raise ValidationError("That item is no longer in your cart.")
        self.impl.update(user_id, line_id, qty)

    def remove(self, user_id, line_id):
        self.impl.remove(user_id, line_id)
