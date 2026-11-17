"""AdminDTO: categories (POSTGRES_CATEGORIES) and order status changes."""
from dataclasses import dataclass

from Interfaces.PayServiceInterface.PayDTO import ORDER_STATUSES
from Interfaces.Validators import SLUG_RE, ValidationError, clean_text, parse_int


@dataclass(frozen=True)
class CategoryForm:
    name: str
    slug: str
    parent_id: int | None
    is_active: bool

    @classmethod
    def from_form(cls, form):
        errors = []
        try:
            name = clean_text(form.get("name"), 60, field="Name", required=True)
        except ValidationError as e:
            errors += e.errors
            name = ""
        slug = str(form.get("slug", "")).strip().lower()
        if not (1 <= len(slug) <= 60 and SLUG_RE.fullmatch(slug)):
            errors.append("Slug: lowercase letters, numbers and dashes only.")
        raw_parent = form.get("parent_id", "")
        parent_id = None
        if raw_parent not in ("", "0", None):
            parent_id = parse_int(raw_parent, min_value=1, max_value=10**6)
            if parent_id is None:
                errors.append("Choose a valid parent category.")
        if errors:
            raise ValidationError(errors)
        return cls(name=name, slug=slug, parent_id=parent_id, is_active=form.get("is_active") == "1")


@dataclass(frozen=True)
class CategoryRowDTO:
    category_id: int
    parent_id: int | None
    name: str
    slug: str
    is_active: bool
    depth: int
    product_count: int


@dataclass(frozen=True)
class StatusForm:
    new_status: str

    @classmethod
    def from_form(cls, form):
        status = form.get("new_status", "")
        if status not in ORDER_STATUSES:
            raise ValidationError("Choose a valid status.")
        return cls(new_status=status)
