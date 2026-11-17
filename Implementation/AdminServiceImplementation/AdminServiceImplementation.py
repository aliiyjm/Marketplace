"""Administration of categories (POSTGRES_CATEGORIES)."""
from collections import Counter

from Implementation.DataSource.Mappers import search_text
from Interfaces.AdminServiceInterface.AdminDTO import CategoryRowDTO
from Interfaces.AdminServiceInterface.AdminServiceInterface import AdminServiceInterface
from Interfaces.Validators import ValidationError


class AdminServiceImplementation(AdminServiceInterface):
    def __init__(self, store):
        self.store = store

    def _counts(self):
        return Counter(p["category_id"] for p in self.store.products.values() if p["status"] != "deleted")

    def _row(self, c, counts):
        children = [x["category_id"] for x in self.store.categories.values() if x["parent_id"] == c["category_id"]]
        total = counts[c["category_id"]] + sum(counts[cid] for cid in children)
        return CategoryRowDTO(c["category_id"], c["parent_id"], c["name"], c["slug"], c["is_active"],
                              0 if c["parent_id"] is None else 1, total)

    def category_rows(self):
        counts = self._counts()
        cats = sorted(self.store.categories.values(), key=lambda c: c["name"])
        rows = []
        for top in (c for c in cats if c["parent_id"] is None):
            rows.append(self._row(top, counts))
            rows += [self._row(c, counts) for c in cats if c["parent_id"] == top["category_id"]]
        return rows

    def get_category(self, category_id):
        c = self.store.categories.get(category_id)
        return self._row(c, self._counts()) if c else None

    def _validate(self, form, category_id=None):
        other = self.store.category_by_slug(form.slug)
        if other and other["category_id"] != category_id:
            raise ValidationError("That slug is already in use.")
        if form.parent_id is not None:
            parent = self.store.categories.get(form.parent_id)
            if not parent or parent["parent_id"] is not None:
                raise ValidationError("The parent must be a top-level category.")
            if form.parent_id == category_id:
                raise ValidationError("A category cannot be its own parent.")
            if category_id and any(c["parent_id"] == category_id for c in self.store.categories.values()):
                raise ValidationError("This category has subcategories, so it must stay top-level.")

    def create_category(self, form):
        with self.store.lock:
            self._validate(form)
            cid = self.store.next_id("categories")
            self.store.categories[cid] = {"category_id": cid, "parent_id": form.parent_id, "name": form.name,
                                          "slug": form.slug, "is_active": form.is_active}
        return cid

    def update_category(self, category_id, form):
        with self.store.lock:
            if category_id not in self.store.categories:
                raise ValidationError("Category not found.")
            self._validate(form, category_id)
            self.store.categories[category_id].update(parent_id=form.parent_id, name=form.name,
                                                      slug=form.slug, is_active=form.is_active)
            # Category names are part of the search text: refresh affected products.
            scope = {category_id} | {c["category_id"] for c in self.store.categories.values()
                                     if c["parent_id"] == category_id}
            for p in self.store.products.values():
                if p["category_id"] in scope:
                    p["_search"] = search_text(self.store, p)

    def toggle_category(self, category_id):
        with self.store.lock:
            c = self.store.categories.get(category_id)
            if not c:
                raise ValidationError("Category not found.")
            c["is_active"] = not c["is_active"]
            return c["is_active"]

    def top_categories(self):
        return [(c["category_id"], c["name"]) for c in sorted(self.store.categories.values(), key=lambda c: c["name"])
                if c["parent_id"] is None]
