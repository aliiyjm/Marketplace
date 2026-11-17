"""Shared validation helpers used by every DTO.

Rule of thumb: a DTO only accepts the fields it declares (whitelist), trims
and length-limits text, and converts numbers with explicit bounds. Anything
else is rejected before it reaches the Implementation layer.
"""
import re
import unicodedata
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
IVA_RATE = Decimal("0.16")

EMAIL_RE = re.compile(r"[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,24}")
USER_ID_RE = re.compile(r"[A-Za-z0-9_-]{1,30}")
PRODUCT_ID_RE = re.compile(r"PRD-\d{6}")
SLUG_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
# Mexican RFC: 3 letters (company) or 4 (person) + date + 3-char homoclave.
RFC_RE = re.compile(r"[A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3}")
_CONTROL = {c for c in map(chr, range(32)) if c not in "\n\t"}


class ValidationError(ValueError):
    """Raised by DTOs and services. Messages are safe to show to the user."""

    def __init__(self, errors):
        self.errors = list(errors) if isinstance(errors, (list, tuple)) else [str(errors)]
        super().__init__("; ".join(self.errors))


def clean_text(value, max_len, *, field="Field", required=False, multiline=False):
    """Trim, drop control characters and enforce a maximum length."""
    text = "" if value is None else str(value)
    text = "".join(c for c in text if c not in _CONTROL)
    if not multiline:
        text = " ".join(text.split())
    text = text.strip()
    if required and not text:
        raise ValidationError(f"{field} is required.")
    if len(text) > max_len:
        raise ValidationError(f"{field} must be at most {max_len} characters.")
    return text


def parse_int(value, *, default=None, min_value=None, max_value=None, field="Value", strict=False):
    """Parse an int within bounds. With strict=False bad input returns default."""
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        if strict:
            raise ValidationError(f"{field} must be a whole number.") from None
        return default
    if (min_value is not None and number < min_value) or (max_value is not None and number > max_value):
        if strict:
            raise ValidationError(f"{field} must be between {min_value} and {max_value}.")
        return default
    return number


def parse_money(value, *, field="Price", min_value=Decimal("0.01"), max_value=Decimal("999999.99"),
                strict=True, default=None):
    """Money is always Decimal with two decimals (never float)."""
    try:
        amount = Decimal(str(value).strip().replace(",", "")).quantize(CENT, rounding=ROUND_HALF_UP)
        if not amount.is_finite():
            raise InvalidOperation
    except (InvalidOperation, ValueError, TypeError):
        if strict:
            raise ValidationError(f"{field} must be a valid amount.") from None
        return default
    if amount < min_value or amount > max_value:
        if strict:
            raise ValidationError(f"{field} must be between {min_value} and {max_value}.")
        return default
    return amount


def money(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def split_iva(gross):
    """Prices include IVA. Returns (subtotal, tax, total) for MYSQL_ORDERS."""
    total = money(gross)
    subtotal = money(total / (1 + IVA_RATE))
    return subtotal, total - subtotal, total


def normalize(text):
    """Lowercase and strip accents so 'cafe' matches 'Café'."""
    base = unicodedata.normalize("NFD", str(text).lower())
    return "".join(c for c in base if unicodedata.category(c) != "Mn")


def valid_email(email):
    return len(email) <= 120 and bool(EMAIL_RE.fullmatch(email))
