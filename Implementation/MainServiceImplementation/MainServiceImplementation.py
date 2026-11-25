"""Business logic for MainService: catalog search, product page and reviews."""
import math

from Implementation.DataSource.Mappers import (is_public, product_to_dto,
                                               refresh_rating, review_to_dto,
                                               search_text)
from Interfaces.MainServiceInterface.MainDTO import (PER_PAGE, CatalogPage,
                                                     CategoryDTO, RatingSummaryDTO,
                                                     SellerSummaryDTO, valid_product_id)
from Interfaces.MainServiceInterface.MainServiceInterface import MainServiceInterface
from Interfaces.DataInterfaces.RepositoryErrors import DuplicateKey
from Interfaces.ProductServiceInterface.ProductServiceInterface import ProductStoreError
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
        self.store.reload_meta()          # PostgreSQL: refresh the cached categories every minute
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
        self.store.reload_meta()
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
        if not p:
            return None
        p = self._refresh(p)
        if not p or not is_public(self.store, p):
            return None
        return product_to_dto(self.store, p)

    def _refresh(self, cached):
        """Product page: read the product again from the database so price and
        stock are current. If the database cannot be reached, show the cache."""
        try:
            fresh = self.store.product_repo.get(cached["product_id"])
        except ProductStoreError:
            return cached
        if fresh is cached:              # memory backend: the cache is the data
            return cached
        if fresh is None:                # removed from the database
            cached["status"] = "deleted"
            return None
        fresh.update(rating=cached.get("rating"), review_count=cached.get("review_count", 0))
        fresh["_search"] = search_text(self.store, fresh)
        with self.store.lock:
            self.store.products[fresh["product_id"]] = fresh
        return fresh

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

    # ---------- reviews (PostgreSQL through store.ops) ----------
    def reviews(self, product_id, limit=5):
        return [review_to_dto(self.store, r) for r in self.store.ops.reviews_for(product_id, limit)]

    def rating_summary(self, product_id):
        counts = self.store.ops.rating_counts(product_id)
        count = sum(counts.values())
        dist = []
        for stars in range(5, 0, -1):
            n = counts.get(stars, 0)
            dist.append((stars, n, round(n * 100 / count) if count else 0))
        avg = round(sum(st * n for st, n in counts.items()) / count, 1) if count else None
        return RatingSummaryDTO(average=avg, count=count, distribution=dist)

    def _bought(self, user_id, product_id):
        return self.store.finance.has_bought(user_id, product_id)

    def _reviewed(self, user_id, product_id):
        return self.store.ops.has_reviewed(user_id, product_id)

    def can_review(self, user_id, product_id):
        return bool(user_id) and self._bought(user_id, product_id) and not self._reviewed(user_id, product_id)

    def add_review(self, user_id, product_id, form):
        if self.get_product(product_id) is None:
            raise ValidationError("Product not found.")
        if not self._bought(user_id, product_id):
            raise ValidationError("Only customers who bought this product can review it.")
        if self._reviewed(user_id, product_id):
            raise ValidationError("You already reviewed this product.")
        try:
            review_id = self.store.ops.add_review({
                "product_id": product_id, "buyer_id": user_id, "rating": form.rating,
                "comment": form.comment, "created_at": self.store.now().isoformat()})
        except DuplicateKey:
            raise ValidationError("You already reviewed this product.") from None
        refresh_rating(self.store, product_id)
        return review_id

    def product_photo(self, key):
        return self.store.product_repo.read_image(key)
