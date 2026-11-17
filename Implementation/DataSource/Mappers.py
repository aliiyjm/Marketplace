"""Convert raw records into DTOs. Shared by every Implementation class so the
View always receives the same shapes."""
from Interfaces.MainServiceInterface.MainDTO import ProductDTO, ReviewDTO
from Interfaces.Validators import normalize


def product_to_dto(store, p):
    cat = store.categories.get(p["category_id"], {})
    parent = store.categories.get(cat.get("parent_id"), {})
    seller = store.sellers.get(p["seller_id"], {})
    return ProductDTO(
        product_id=p["product_id"], name=p["name"], description=p.get("description", ""),
        category_id=p["category_id"], category_name=cat.get("name", ""),
        category_slug=cat.get("slug", ""),
        parent_category_name=parent.get("name", ""), parent_category_slug=parent.get("slug", ""),
        seller_id=p["seller_id"], store_name=seller.get("store_name", ""),
        price=p["price"], stock=p["stock"], status=p["status"],
        sizes=tuple(p["sizes"]), colors=tuple(p["colors"]),
        color_key=p.get("color_key", "gray"), kind=p.get("kind", "tshirt"),
        attributes=dict(p.get("attributes", {})),
        rating=p.get("rating"), review_count=p.get("review_count", 0),
        threshold_limit=p.get("threshold_limit", 0), created_at=p.get("created_at", ""),
    )


def review_to_dto(store, r):
    buyer = store.users.get(r["buyer_id"], {})
    name = buyer.get("name", "Customer")
    parts = name.split()
    short = parts[0] if len(parts) < 2 else f"{parts[0]} {parts[1][0]}."
    return ReviewDTO(review_id=r["review_id"], product_id=r["product_id"], buyer_id=r["buyer_id"],
                     buyer_name=short,   # first name + initial: no full names in public
                     rating=r["rating"], comment=r["comment"], created_at=r["created_at"])


def is_public(store, p):
    """Visible in the catalog: active product, active category chain, active seller."""
    if p["status"] != "active":
        return False
    cat = store.categories.get(p["category_id"])
    if not cat or not cat["is_active"]:
        return False
    parent = store.categories.get(cat.get("parent_id"))
    if parent and not parent["is_active"]:
        return False
    seller = store.sellers.get(p["seller_id"])
    return bool(seller and seller["status"] == "active")


def search_text(store, record):
    cat = store.categories.get(record["category_id"], {})
    parent = store.categories.get(cat.get("parent_id"), {})
    seller = store.sellers.get(record["seller_id"], {})
    return normalize(" ".join([record["name"], cat.get("name", ""), parent.get("name", ""),
                               " ".join(record["colors"]), seller.get("store_name", ""),
                               record["product_id"]]))


def refresh_rating(store, product_id):
    reviews = store.reviews_by_product.get(product_id, [])
    product = store.products[product_id]
    product["review_count"] = len(reviews)
    product["rating"] = round(sum(x["rating"] for x in reviews) / len(reviews), 1) if reviews else None
