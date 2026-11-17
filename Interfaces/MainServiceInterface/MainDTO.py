"""MainDTO: shape of the data used by MainService (home, catalog, product page).

Products live in DynamoDB (flexible JSON attributes), categories and reviews
in PostgreSQL. These DTOs are what the View receives; it never touches raw
records.
"""
from dataclasses import dataclass, field
from decimal import Decimal

from Interfaces.Validators import (PRODUCT_ID_RE, ValidationError, clean_text,
                                   parse_int, parse_money)

PER_PAGE = 24
# Garment colors offered by the store: name -> CSS key (swatches and placeholders).
COLOR_PALETTE = {
    "Black": "black", "White": "white", "Navy": "navy", "Gray": "gray", "Beige": "beige",
    "Olive": "olive", "Burgundy": "burgundy", "Sky blue": "sky", "Red": "red", "Pink": "pink",
    "Mustard": "mustard", "Brown": "brown", "Denim blue": "denim", "Green": "green",
    "Lilac": "lilac", "Cream": "cream",
}
_PALETTE_LOWER = {k.lower(): v for k, v in COLOR_PALETTE.items()}


def color_key(name):
    return _PALETTE_LOWER.get(str(name).lower(), "gray")
SORTS = {
    "relevance": "Most relevant",
    "price_asc": "Price: low to high",
    "price_desc": "Price: high to low",
    "rating": "Best rated",
    "newest": "Newest",
}


@dataclass(frozen=True)
class CategoryDTO:
    category_id: int
    parent_id: int | None
    name: str
    slug: str
    is_active: bool
    children: tuple = ()


@dataclass(frozen=True)
class ProductDTO:
    product_id: str
    name: str
    description: str
    category_id: int
    category_name: str
    parent_category_name: str
    parent_category_slug: str
    category_slug: str
    seller_id: str
    store_name: str
    price: Decimal
    stock: int
    status: str
    sizes: tuple
    colors: tuple
    color_key: str          # CSS class used for the garment placeholder
    kind: str               # garment silhouette: tshirt, dress, pants, ...
    attributes: dict        # the rest of the dynamic JSON (material, fit, ...)
    rating: float | None
    review_count: int
    threshold_limit: int
    created_at: str

    @property
    def in_stock(self):
        return self.stock > 0

    @property
    def swatches(self):
        return [(c, color_key(c)) for c in self.colors]

    @property
    def specs(self):
        """Attributes as (label, value) pairs for the specifications table."""
        rows = [("Sizes", ", ".join(self.sizes)), ("Colors", ", ".join(self.colors))]
        rows += [(k, v) for k, v in self.attributes.items()]
        return rows


@dataclass(frozen=True)
class SellerSummaryDTO:
    seller_id: str
    store_name: str
    rating: float
    status: str


@dataclass(frozen=True)
class ReviewDTO:
    review_id: int
    product_id: str
    buyer_id: str
    buyer_name: str
    rating: int
    comment: str
    created_at: str


@dataclass(frozen=True)
class CatalogQuery:
    """Search and filter options for the home page. Invalid values are ignored
    (falls back to defaults) instead of erroring, because they come from the URL."""
    q: str = ""
    category: str = ""
    min_price: Decimal | None = None
    max_price: Decimal | None = None
    min_rating: int | None = None
    in_stock: bool = False
    sort: str = "relevance"
    page: int = 1

    @classmethod
    def from_args(cls, args):
        try:
            q = clean_text(args.get("q", ""), 80)
        except ValidationError:
            q = ""
        category = args.get("cat", "").strip()[:60]
        sort = args.get("sort", "relevance")
        money_opts = dict(strict=False, min_value=Decimal("0"), max_value=Decimal("999999"))
        return cls(
            q=q,
            category=category,
            min_price=parse_money(args.get("min", ""), **money_opts),
            max_price=parse_money(args.get("max", ""), **money_opts),
            min_rating=parse_int(args.get("rating"), min_value=1, max_value=5),
            in_stock=args.get("instock") == "1",
            sort=sort if sort in SORTS else "relevance",
            page=parse_int(args.get("page"), default=1, min_value=1, max_value=100000),
        )

    def url_args(self, **changes):
        """Query-string args to build links (pagination, sorting) keeping filters."""
        data = {"q": self.q, "cat": self.category,
                "min": self.min_price, "max": self.max_price,
                "rating": self.min_rating, "instock": "1" if self.in_stock else None,
                "sort": None if self.sort == "relevance" else self.sort, "page": self.page}
        data.update(changes)
        return {k: v for k, v in data.items() if v not in (None, "") and not (k == "page" and v == 1)}


@dataclass(frozen=True)
class CatalogPage:
    items: list
    total: int
    page: int
    pages: int
    query: CatalogQuery
    category: CategoryDTO | None = None

    @property
    def first(self):
        return 0 if not self.total else (self.page - 1) * PER_PAGE + 1

    @property
    def last(self):
        return min(self.page * PER_PAGE, self.total)

    @property
    def page_links(self):
        """Compact list of page numbers with None for the '…' gaps."""
        wanted = {1, self.pages, self.page - 1, self.page, self.page + 1}
        numbers = sorted(n for n in wanted if 1 <= n <= self.pages)
        out, prev = [], 0
        for n in numbers:
            if n - prev > 1:
                out.append(None)
            out.append(n)
            prev = n
        return out


@dataclass(frozen=True)
class RatingSummaryDTO:
    average: float | None
    count: int
    distribution: list = field(default_factory=list)   # [(stars, count, percent)]


@dataclass(frozen=True)
class ProductPageDTO:
    product: ProductDTO
    seller: SellerSummaryDTO | None
    reviews: list
    rating: RatingSummaryDTO
    related: list
    can_review: bool


@dataclass(frozen=True)
class ReviewForm:
    rating: int
    comment: str

    @classmethod
    def from_form(cls, form):
        rating = parse_int(form.get("rating"), min_value=1, max_value=5, field="Rating", strict=True)
        comment = clean_text(form.get("comment"), 500, field="Comment", required=True, multiline=True)
        return cls(rating=rating, comment=comment)


def valid_product_id(product_id):
    return bool(PRODUCT_ID_RE.fullmatch(product_id or ""))
