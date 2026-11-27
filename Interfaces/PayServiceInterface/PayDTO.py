"""PayDTO: checkout forms and orders (MySQL: ORDERS, ORDER_ITEMS, STATUS_HISTORY,
PAYMENTS, SHIPPING, INVOICES)."""
import re
import secrets
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from Interfaces.Validators import ValidationError, clean_text

MX_STATES = (
    "Aguascalientes", "Baja California", "Baja California Sur", "Campeche", "Chiapas",
    "Chihuahua", "Ciudad de México", "Coahuila", "Colima", "Durango", "Estado de México",
    "Guanajuato", "Guerrero", "Hidalgo", "Jalisco", "Michoacán", "Morelos", "Nayarit",
    "Nuevo León", "Oaxaca", "Puebla", "Querétaro", "Quintana Roo", "San Luis Potosí",
    "Sinaloa", "Sonora", "Tabasco", "Tamaulipas", "Tlaxcala", "Veracruz", "Yucatán", "Zacatecas",
)
PAYMENT_METHODS = {
    "card": ("Credit / debit card", "Visa, Mastercard, Amex"),
    "spei": ("Bank transfer", "SPEI reference"),
    "cash": ("Cash at store", "Pay within 48 h"),
}
ORDER_STATUSES = ("Pending", "Paid", "Shipped", "Delivered", "Cancelled")
# Which status can follow which (enforced by PayServiceImplementation.change_status).
ALLOWED_TRANSITIONS = {
    "Pending": ("Paid", "Cancelled"),
    "Paid": ("Shipped", "Cancelled"),
    "Shipped": ("Delivered",),
    "Delivered": (),
    "Cancelled": (),
}
ZIP_RE = re.compile(r"\d{5}")


def luhn_ok(number):
    total, parity = 0, len(number) % 2
    for i, ch in enumerate(number):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


@dataclass(frozen=True)
class CheckoutForm:
    street: str
    city: str
    state: str
    zip_code: str
    method: str
    card_last4: str = ""     # last 4 digits, shown to the buyer ("Card · ending 4242")
    card_token: str = ""     # opaque payment-method token (tok_ + 32 hex): never the card number

    def __repr__(self):
        return f"CheckoutForm(method={self.method!r}, zip={self.zip_code!r})"

    @classmethod
    def from_form(cls, form, today=None):
        errors = []

        def text(name, label, max_len):
            try:
                return clean_text(form.get(name), max_len, field=label, required=True)
            except ValidationError as e:
                errors.extend(e.errors)
                return ""

        street = text("street", "Street and number", 120)
        city = text("city", "City", 60)
        state = form.get("state", "")
        if state not in MX_STATES:
            errors.append("Choose a valid state.")
        zip_code = str(form.get("zip_code", "")).strip()
        if not ZIP_RE.fullmatch(zip_code):
            errors.append("ZIP code must have 5 digits.")
        method = form.get("method", "")
        if method not in PAYMENT_METHODS:
            errors.append("Choose a payment method.")
        last4, token = "", ""
        if method == "card":
            # Card data is validated and then discarded. Tokenization: like a payment
            # provider would, the card is exchanged for an opaque random token; only the
            # token and the last 4 digits are kept. The number and the CVV never leave
            # this method (simulated provider: no real charge is made).
            number = re.sub(r"[\s-]", "", str(form.get("card_number", "")))
            if not (number.isdigit() and 13 <= len(number) <= 19 and luhn_ok(number)):
                errors.append("Enter a valid card number.")
            else:
                last4, token = number[-4:], "tok_" + secrets.token_hex(16)
            exp = re.fullmatch(r"\s*(\d{2})\s*/\s*(\d{2})\s*", str(form.get("expiry", "")))
            today = today or date.today()
            if not exp or not 1 <= int(exp.group(1)) <= 12:
                errors.append("Expiry must look like MM/YY.")
            elif (2000 + int(exp.group(2)), int(exp.group(1))) < (today.year, today.month):
                errors.append("The card is expired.")
            if not re.fullmatch(r"\d{3,4}", str(form.get("cvv", ""))):
                errors.append("CVV must have 3 or 4 digits.")
        if errors:
            raise ValidationError(errors)
        return cls(street=street, city=city, state=state, zip_code=zip_code,
                   method=method, card_last4=last4, card_token=token)


@dataclass(frozen=True)
class OrderItemDTO:
    item_id: int
    product_id: str
    name: str
    quantity: int
    unit_price: Decimal
    size: str
    color: str
    color_key: str
    kind: str
    image_url: str = ""

    @property
    def subtotal(self):
        return self.unit_price * self.quantity


@dataclass(frozen=True)
class StatusChangeDTO:
    history_id: int
    old_status: str | None
    new_status: str
    changed_at: str


@dataclass(frozen=True)
class PaymentDTO:
    payment_id: int
    payment_method: str
    status: str
    card_last4: str

    @property
    def label(self):
        name = PAYMENT_METHODS.get(self.payment_method, (self.payment_method,))[0]
        return f"{name} · ending {self.card_last4}" if self.card_last4 else name


@dataclass(frozen=True)
class ShippingDTO:
    address_id: int
    street: str
    city: str
    state: str
    zip_code: str

    @property
    def one_line(self):
        return f"{self.street}, {self.city}, {self.state}, {self.zip_code}"


@dataclass(frozen=True)
class InvoiceDTO:
    invoice_id: int
    pdf_receipt_url: str
    issued_at: str

    @property
    def code(self):
        return f"INV-{self.invoice_id}"


@dataclass(frozen=True)
class BuyerHistoryDTO:
    """Consolidated history of a buyer (sp_buyer_sales_history). Cancelled orders do not count."""
    orders_count: int
    active_orders: int
    total_spent: Decimal
    units_bought: int


@dataclass(frozen=True)
class OrderDTO:
    order_id: int
    buyer_id: str
    buyer_name: str
    created_at: str
    status: str
    subtotal: Decimal
    tax_amount: Decimal
    total_amount: Decimal
    items: list = field(default_factory=list)
    history: list = field(default_factory=list)
    payment: PaymentDTO | None = None
    shipping: ShippingDTO | None = None
    invoice: InvoiceDTO | None = None

    @property
    def code(self):
        return f"ORD-{self.order_id}"

    @property
    def item_count(self):
        return sum(i.quantity for i in self.items)

    @property
    def next_statuses(self):
        return ALLOWED_TRANSITIONS.get(self.status, ())
