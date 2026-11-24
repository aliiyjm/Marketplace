"""ProductDTO: one product as it is stored in DynamoDB (single-table MarketplaceDB).

    PK      = PRODUCT#<product_id>       SK     = METADATA
    GSI1PK  = CATEGORY#<category slug>   GSI1SK = PRICE#<0000499.00>   (browse a
              category sorted by price with one Query on the GSI1 index)

The same class validates the payload in the Lambda (never trust the caller),
converts to/from the DynamoDB item and to/from the record the Flask app keeps.
Prices travel as strings ("499.00") so no float rounding ever happens.
"""
import re
from dataclasses import dataclass, field
from decimal import Decimal

from Interfaces.MainServiceInterface.MainDTO import COLOR_PALETTE
from Interfaces.Validators import (PRODUCT_ID_RE, SLUG_RE, ValidationError,
                                   clean_text, parse_int, parse_money)

STATUSES = ("active", "draft", "deleted")
KINDS = ("tshirt", "dress", "pants", "jacket", "shoe", "bag", "cap", "glasses", "belt")
SELLER_ID_RE = re.compile(r"SEL-\d{4,8}")
ATTR_KEY_RE = re.compile(r"[A-Za-zÀ-ÿ0-9][A-Za-zÀ-ÿ0-9 &/().-]{0,39}")
MAX_ATTRIBUTES = 15
SK_PRODUCT = "METADATA"
PK_PREFIX = "PRODUCT#"


def _str_list(raw, *, name, max_items, max_len):
    if not isinstance(raw, (list, tuple)) or not raw:
        raise ValidationError(f"{name} must be a non-empty list.")
    if len(raw) > max_items:
        raise ValidationError(f"{name}: at most {max_items} values.")
    out = []
    for value in raw:
        if not isinstance(value, str):
            raise ValidationError(f"{name} must contain text only.")
        out.append(clean_text(value, max_len, field=name, required=True))
    return tuple(out)


@dataclass(frozen=True)
class ProductItem:
    product_id: str
    name: str
    description: str
    category_id: int
    category_slug: str
    seller_id: str
    price: Decimal
    stock: int
    status: str
    sizes: tuple
    colors: tuple
    color_key: str
    kind: str
    attributes: dict = field(default_factory=dict)
    threshold_limit: int = 0
    created_at: str = ""

    # ---------- validation of untrusted input (API payload) ----------
    @classmethod
    def from_payload(cls, data):
        if not isinstance(data, dict):
            raise ValidationError("Product must be a JSON object.")
        errors = []

        def run(fn, default=None):
            try:
                return fn()
            except ValidationError as e:
                errors.extend(e.errors)
                return default

        product_id = str(data.get("product_id", ""))
        if not PRODUCT_ID_RE.fullmatch(product_id):
            errors.append("product_id must look like PRD-000123.")
        seller_id = str(data.get("seller_id", ""))
        if not SELLER_ID_RE.fullmatch(seller_id):
            errors.append("seller_id must look like SEL-0001.")
        slug = str(data.get("category_slug", ""))
        if not (len(slug) <= 60 and SLUG_RE.fullmatch(slug)):
            errors.append("category_slug is invalid.")
        status = data.get("status", "active")
        if status not in STATUSES:
            errors.append("status must be active, draft or deleted.")
        kind = data.get("kind", "tshirt")
        if kind not in KINDS:
            errors.append("kind is invalid.")
        color_key = data.get("color_key", "gray")
        if color_key not in COLOR_PALETTE.values():
            errors.append("color_key is invalid.")

        attributes = {}
        raw_attrs = data.get("attributes", {})
        if not isinstance(raw_attrs, dict) or len(raw_attrs) > MAX_ATTRIBUTES:
            errors.append(f"attributes must be an object with at most {MAX_ATTRIBUTES} entries.")
        else:
            for key, value in raw_attrs.items():
                if not (isinstance(key, str) and ATTR_KEY_RE.fullmatch(key) and isinstance(value, str)):
                    errors.append("attributes: names must be short text and values text.")
                    break
                attributes[key] = run(lambda v=value: clean_text(v, 120, field="Attribute value", required=True), "")

        item = dict(
            product_id=product_id, seller_id=seller_id, category_slug=slug, status=status,
            kind=kind, color_key=color_key, attributes=attributes,
            name=run(lambda: clean_text(data.get("name"), 120, field="name", required=True), ""),
            description=run(lambda: clean_text(data.get("description", ""), 2000, field="description",
                                               multiline=True), ""),
            category_id=run(lambda: parse_int(data.get("category_id"), field="category_id", min_value=1,
                                              max_value=10**6, strict=True), 0),
            price=run(lambda: parse_money(data.get("price"), field="price"), Decimal("0")),
            stock=run(lambda: parse_int(data.get("stock"), field="stock", min_value=0, max_value=99999,
                                        strict=True), 0),
            threshold_limit=run(lambda: parse_int(data.get("threshold_limit", 0), field="threshold_limit",
                                                  min_value=0, max_value=9999, strict=True), 0),
            sizes=run(lambda: _str_list(data.get("sizes"), name="sizes", max_items=20, max_len=20), ()),
            colors=run(lambda: _str_list(data.get("colors"), name="colors", max_items=12, max_len=30), ()),
            created_at=run(lambda: clean_text(data.get("created_at", ""), 32, field="created_at"), ""),
        )
        if errors:
            raise ValidationError(errors)
        return cls(**item)

    # ---------- DynamoDB ----------
    def to_item(self):
        return {
            "PK": PK_PREFIX + self.product_id, "SK": SK_PRODUCT, "entity": "PRODUCT",
            "GSI1PK": f"CATEGORY#{self.category_slug}", "GSI1SK": f"PRICE#{self.price:010.2f}",
            "product_id": self.product_id, "name": self.name, "description": self.description,
            "category_id": self.category_id, "category_slug": self.category_slug,
            "seller_id": self.seller_id, "price": self.price, "stock": self.stock,
            "status": self.status, "sizes": list(self.sizes), "colors": list(self.colors),
            "color_key": self.color_key, "kind": self.kind, "attributes": dict(self.attributes),
            "threshold_limit": self.threshold_limit, "created_at": self.created_at,
        }

    @classmethod
    def from_item(cls, item):
        """Item read from DynamoDB (boto3 returns numbers as Decimal)."""
        return cls(
            product_id=item["product_id"], name=item.get("name", ""), description=item.get("description", ""),
            category_id=int(item.get("category_id", 0)), category_slug=item.get("category_slug", ""),
            seller_id=item.get("seller_id", ""), price=Decimal(str(item.get("price", "0"))).quantize(Decimal("0.01")),
            stock=int(item.get("stock", 0)), status=item.get("status", "active"),
            sizes=tuple(item.get("sizes", [])), colors=tuple(item.get("colors", [])),
            color_key=item.get("color_key", "gray"), kind=item.get("kind", "tshirt"),
            attributes=dict(item.get("attributes", {})), threshold_limit=int(item.get("threshold_limit", 0)),
            created_at=item.get("created_at", ""),
        )

    # ---------- JSON (API Gateway) ----------
    def to_payload(self):
        data = self.to_item()
        for key in ("PK", "SK", "entity", "GSI1PK", "GSI1SK"):
            data.pop(key)
        data["price"] = f"{self.price:.2f}"
        return data

    # ---------- Flask in-memory record ----------
    def to_record(self):
        record = self.to_item()
        record.pop("entity")
        record.pop("category_slug")
        record.update(rating=None, review_count=0)
        return record

    @classmethod
    def from_record(cls, record, category_slug):
        data = {k: record.get(k) for k in (
            "product_id", "name", "description", "category_id", "seller_id", "status", "kind",
            "color_key", "attributes", "threshold_limit", "created_at", "stock")}
        data.update(category_slug=category_slug, price=f"{record['price']:.2f}",
                    sizes=list(record["sizes"]), colors=list(record["colors"]))
        return cls.from_payload(data)
