"""SellerDTO: seller center (products in DynamoDB, POSTGRES_ANALYTICS, POSTGRES_ALERTS)."""
import re
from dataclasses import dataclass, field

from Interfaces.Validators import ValidationError, clean_text, parse_int, parse_money

MAX_ATTRIBUTES = 15
ATTR_KEY_RE = re.compile(r"[A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9 &/().-]{0,39}")
RESERVED_KEYS = {"sizes", "colors", "size", "color"}


def _split_list(raw, *, field_name, max_items, max_len):
    items = []
    for part in str(raw or "").split(","):
        value = clean_text(part, max_len, field=field_name)
        if value and value.lower() not in (i.lower() for i in items):
            items.append(value)
    if not items:
        raise ValidationError(f"Add at least one value in {field_name}.")
    if len(items) > max_items:
        raise ValidationError(f"{field_name}: at most {max_items} values.")
    return tuple(items)


@dataclass(frozen=True)
class ProductForm:
    """Whitelisted fields a seller can write. seller_id is NEVER taken from the
    form: it comes from the signed-in session."""
    name: str
    description: str
    category_id: int
    price: object
    stock: int
    threshold_limit: int
    sizes: tuple
    colors: tuple
    attributes: dict = field(default_factory=dict)
    visible: bool = True

    @classmethod
    def from_form(cls, form):
        errors = []

        def run(fn, default):
            try:
                return fn()
            except ValidationError as e:
                errors.extend(e.errors)
                return default

        name = run(lambda: clean_text(form.get("name"), 120, field="Product name", required=True), "")
        description = run(lambda: clean_text(form.get("description"), 2000, field="Description",
                                             multiline=True), "")
        category_id = run(lambda: parse_int(form.get("category_id"), field="Category",
                                            min_value=1, max_value=10**6, strict=True), 0)
        price = run(lambda: parse_money(form.get("price"), field="Price"), None)
        stock = run(lambda: parse_int(form.get("stock"), field="Stock", min_value=0,
                                      max_value=99999, strict=True), 0)
        threshold = run(lambda: parse_int(form.get("threshold_limit"), field="Alert threshold",
                                          min_value=0, max_value=9999, strict=True), 0)
        sizes = run(lambda: _split_list(form.get("sizes"), field_name="Sizes", max_items=20, max_len=12), ())
        colors = run(lambda: _split_list(form.get("colors"), field_name="Colors", max_items=12, max_len=30), ())

        keys = form.getlist("attr_key") if hasattr(form, "getlist") else form.get("attr_key", [])
        values = form.getlist("attr_value") if hasattr(form, "getlist") else form.get("attr_value", [])
        attributes = {}
        for raw_key, raw_value in zip(keys, values):
            key = " ".join(str(raw_key).split())
            value = run(lambda v=raw_value: clean_text(v, 120, field="Attribute value"), "")
            if not key and not value:
                continue
            if not ATTR_KEY_RE.fullmatch(key):
                errors.append(f"Attribute name “{key[:40]}” has invalid characters.")
            elif key.lower() in RESERVED_KEYS:
                errors.append("Sizes and colors have their own fields.")
            elif not value:
                errors.append(f"Attribute “{key}” needs a value.")
            else:
                attributes[key[:1].upper() + key[1:]] = value
        if len(attributes) > MAX_ATTRIBUTES:
            errors.append(f"At most {MAX_ATTRIBUTES} attributes.")
        if errors:
            raise ValidationError(errors)
        return cls(name=name, description=description, category_id=category_id, price=price,
                   stock=stock, threshold_limit=threshold, sizes=sizes, colors=colors,
                   attributes=attributes, visible=form.get("visible") == "1")


@dataclass(frozen=True)
class KpiDTO:
    label: str
    value: str
    note: str
    alert: bool = False


@dataclass(frozen=True)
class DailyUnitsDTO:
    day: str          # "Nov 3"
    units: int
    height: int       # 0..100 step 5 (CSS class, CSP forbids inline styles)


@dataclass(frozen=True)
class TopProductDTO:
    product_id: str
    name: str
    category: str
    units: int


@dataclass(frozen=True)
class AlertDTO:
    alert_id: int
    product_id: str
    product_name: str
    current_stock: int
    threshold_limit: int
    alert_status: str     # Open | Resolved


@dataclass(frozen=True)
class DashboardDTO:
    store_name: str
    kpis: list
    daily: list
    top_products: list
    alerts: list
    period_label: str


@dataclass(frozen=True)
class SellerProductsPage:
    items: list
    total: int
    page: int
    pages: int
