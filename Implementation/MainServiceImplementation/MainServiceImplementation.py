"""Business logic for MainService: catalog search, product page and reviews."""
import math

from Implementation.DataSource.Mappers import (is_public, product_to_dto,
                                               refresh_rating, review_to_dto)
from Interfaces.MainServiceInterface.MainDTO import (PER_PAGE, CatalogPage,
                                                     CategoryDTO, RatingSummaryDTO,
                                                     SellerSummaryDTO, valid_product_id)
from Interfaces.MainServiceInterface.MainServiceInterface import MainServiceInterface
from Interfaces.Validators import ValidationError, normalize

_SORT_KEYS = {
    "relevance": lambda p: (-p["review_count"], p["product_id"]),
    "price_asc": lambda p: (p["price"], p["product_id"]),
    "price_desc": lambda p: (-p["price"], p["product_id"]),
    "rating": lambda p: (-(p["rating"] or 0), -p["review_count"], p["product_id"]),
    "newest": lambda p: (p["created_at"],),
}


class MainServiceImplementation(MainServiceInterface):
    def __init__(self, store):
        self.store = store

    # ---------- categories ----------
    def category_tree(self):
        cats = self.store.categories.values()
        tree = []
        for top in sorted((c for c in cats if c["parent_id"] is None and c["is_active"]),
                          key=lambda c: c["category_id"]):
            children = tuple(self._cat(c) for c in sorted(cats, key=lambda c: c["category_id"])
                             if c["parent_id"] == top["category_id"] and c["is_active"])
            tree.append(self._cat(top, children))
        return tree

    @staticmethod
    def _cat(c, children=()):
        return CategoryDTO(c["category_id"], c["parent_id"], c["name"], c["slug"], c["is_active"], children)

    def _category_scope(self, slug):
        """Selected category + its children (only if the category is active)."""
        cat = self.store.category_by_slug(slug) if slug else None
        if not cat or not cat["is_active"]:
            return None, None
        ids = {cat["category_id"]} | {c["category_id"] for c in self.store.categories.values()
                                      if c["parent_id"] == cat["category_id"]}
        return self._cat(cat), ids

    # ---------- catalog ----------
    def search(self, query):
        store = self.store
        category, scope = self._category_scope(query.category)
        terms = normalize(query.q).split()
        with store.lock:
            found = [p for p in store.products.values() if is_public(store, p)]
        if scope is not None:
            found = [p for p in found if p["category_id"] in scope]
        if terms:
            found = [p for p in found if all(t in p["_search"] for t in terms)]
        if query.min_price is not None:
            found = [p for p in found if p["price"] >= query.min_price]
        if query.max_price is not None:
            found = [p for p in found if p["price"] <= query.max_price]
        if query.min_rating:
            found = [p for p in found if (p["rating"] or 0) >= query.min_rating]
        if query.in_stock:
            found = [p for p in found if p["stock"] > 0]
        found.sort(key=_SORT_KEYS[query.sort], reverse=query.sort == "newest")
        total = len(found)
        pages = max(1, math.ceil(total / PER_PAGE))
        page = min(query.page, pages)
        start = (page - 1) * PER_PAGE
        items = [product_to_dto(store, p) for p in found[start:start + PER_PAGE]]
        return CatalogPage(items=items, total=total, page=page, pages=pages, query=query, category=category)

    def get_product(self, product_id):
        if not valid_product_id(product_id):
            return None
        p = self.store.products.get(product_id)
        if not p or not is_public(self.store, p):
            return None
        return product_to_dto(self.store, p)

    def related(self, product, limit=4):
        """Other products of the same subcategory (for the product page)."""
        same = [p for p in self.store.products.values()
                if p["category_id"] == product.category_id and p["product_id"] != product.product_id
                and p["stock"] > 0 and is_public(self.store, p)]
        same.sort(key=_SORT_KEYS["rating"])
        return [product_to_dto(self.store, p) for p in same[:limit]]

    def get_seller(self, seller_id):
        s = self.store.sellers.get(seller_id)
        if not s:
            return None
        return SellerSummaryDTO(s["seller_id"], s["store_name"], s["rating"], s["status"])

    # ---------- reviews ----------
    def reviews(self, product_id, limit=5):
        rows = sorted(self.store.reviews_by_product.get(product_id, []),
                      key=lambda r: r["created_at"], reverse=True)
        return [review_to_dto(self.store, r) for r in rows[:limit]]

    def rating_summary(self, product_id):
        rows = self.store.reviews_by_product.get(product_id, [])
        count = len(rows)
        dist = []
        for stars in range(5, 0, -1):
            n = sum(1 for r in rows if r["rating"] == stars)
            pct = round(n * 100 / count) if count else 0
            dist.append((stars, n, pct))
        avg = round(sum(r["rating"] for r in rows) / count, 1) if count else None
        return RatingSummaryDTO(average=avg, count=count, distribution=dist)

    def _bought(self, user_id, product_id):
        for order in self.store.orders.values():
            if order["buyer_id"] == user_id and order["status"] != "Cancelled":
                if any(i["product_id"] == product_id for i in self.store.order_items[order["order_id"]]):
                    return True
        return False

    def _reviewed(self, user_id, product_id):
        return any(r["buyer_id"] == user_id for r in self.store.reviews_by_product.get(product_id, []))

    def can_review(self, user_id, product_id):
        return bool(user_id) and self._bought(user_id, product_id) and not self._reviewed(user_id, product_id)

    def add_review(self, user_id, product_id, form):
        if self.get_product(product_id) is None:
            raise ValidationError("Product not found.")
        if not self._bought(user_id, product_id):
            raise ValidationError("Only customers who bought this product can review it.")
        with self.store.lock:
            if self._reviewed(user_id, product_id):
                raise ValidationError("You already reviewed this product.")
            review_id = self.store.next_id("reviews")
            review = {"review_id": review_id, "product_id": product_id, "buyer_id": user_id,
                      "rating": form.rating, "comment": form.comment,
                      "created_at": self.store.now().isoformat()}
            self.store.reviews[review_id] = review
            self.store.reviews_by_product[product_id].append(review)
            refresh_rating(self.store, product_id)
        return review_id
