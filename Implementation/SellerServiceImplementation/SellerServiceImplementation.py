"""Seller center: dashboard, product CRUD (DynamoDB items) and stock alerts.

Every method receives the seller_id resolved from the session; product
lookups always check `seller_id` so one seller cannot edit another's items.
"""
import math
from datetime import date, timedelta

from Implementation.DataSource.Inventory import refresh_alert
from Implementation.DataSource.Mappers import product_to_dto, search_text
from Implementation.DataSource.ProductPhotos import prepare_photo
from Interfaces.MainServiceInterface.MainDTO import color_key, valid_product_id
from Interfaces.ProductServiceInterface.ProductServiceInterface import ProductExists
from Interfaces.SellerServiceInterface.SellerDTO import (AlertDTO, DailyUnitsDTO,
                                                         DashboardDTO, KpiDTO,
                                                         SellerProductsPage, TopProductDTO)
from Interfaces.SellerServiceInterface.SellerServiceInterface import SellerServiceInterface
from Interfaces.Validators import ValidationError, normalize

PER_PAGE = 20
KIND_BY_TOP = {"women": "dress", "men": "tshirt", "kids": "tshirt", "shoes": "shoe",
               "accessories": "bag", "sportswear": "pants"}
KIND_BY_SLUG = {"women-tops": "tshirt", "women-jeans": "pants", "women-jackets": "jacket",
                "men-jeans": "pants", "men-jackets": "jacket", "men-hoodies": "jacket",
                "kids-girls": "dress", "acc-hats": "cap", "acc-belts": "belt",
                "acc-sunglasses": "glasses", "sport-bras": "tshirt", "sport-tracksuits": "jacket"}


def _short(day):
    """'Nov 3' (portable: %-d does not exist on Windows)."""
    return f"{day:%b} {day.day}"


class SellerServiceImplementation(SellerServiceInterface):
    def __init__(self, store):
        self.store = store

    def seller_for_user(self, user_id):
        seller = self.store.seller_by_user(user_id)
        return seller["seller_id"] if seller and seller["status"] == "active" else None

    # ---------- dashboard ----------
    def dashboard(self, seller_id, days=30):
        """Sales from analytics and stock alerts, read from store.ops on every request."""
        store = self.store
        store.reload_meta()
        seller = store.sellers[seller_id]
        today = store.now().date()
        since = today - timedelta(days=days - 1)
        per_day = {str(k)[:10]: int(v) for k, v in store.ops.units_by_day(seller_id, since).items()}
        units = sum(per_day.values())

        last14 = [today - timedelta(days=d) for d in range(13, -1, -1)]
        peak = max([per_day.get(d.isoformat(), 0) for d in last14] + [1])
        daily = [DailyUnitsDTO(day=_short(d), units=per_day.get(d.isoformat(), 0),
                               height=int(round(per_day.get(d.isoformat(), 0) / peak * 20)) * 5)
                 for d in last14]

        top = []
        # Consolidated history of the seller (stored function fn_seller_sales_history in PostgreSQL)
        for row in store.ops.seller_history(seller_id, since, 5):
            p = store.products.get(row["product_id"])
            if p:
                dto = product_to_dto(store, p)
                last = date.fromisoformat(str(row["last_sale"])[:10]) if row["last_sale"] else None
                top.append(TopProductDTO(row["product_id"], dto.name,
                                         f"{dto.parent_category_name} › {dto.category_name}", int(row["units"]),
                                         int(row["days_with_sales"]), _short(last) if last else ""))

        alerts = store.ops.alerts_for_seller(seller_id, 6)
        open_alerts = store.ops.count_open_alerts(seller_id)
        active = sum(1 for p in store.products.values()
                     if p["seller_id"] == seller_id and p["status"] == "active")
        rating = float(seller.get("rating") or 0)
        kpis = [
            KpiDTO("Units sold", f"{units:,}", f"last {days} days"),
            KpiDTO("Active products", f"{active:,}", "visible in the catalog"),
            KpiDTO("Store rating", f"{rating:.1f}" if rating else "—", "out of 5"),
            KpiDTO("Open stock alerts", str(open_alerts), "below your threshold", alert=open_alerts > 0),
        ]
        alert_dtos = [AlertDTO(a["alert_id"], a["product_id"],
                               store.products.get(a["product_id"], {}).get("name", a["product_id"]),
                               a["current_stock"], a["threshold_limit"], a["alert_status"])
                      for a in alerts]
        return DashboardDTO(store_name=seller["store_name"], kpis=kpis, daily=daily, top_products=top,
                            alerts=alert_dtos,
                            period_label=f"{_short(last14[0])} – {_short(today)}")

    # ---------- products ----------
    def list_products(self, seller_id, q="", status="", page=1):
        rows = [p for p in self.store.products.values()
                if p["seller_id"] == seller_id and p["status"] != "deleted"]
        if status == "out":
            rows = [p for p in rows if p["stock"] == 0]
        elif status in ("active", "draft"):
            rows = [p for p in rows if p["status"] == status]
        terms = normalize(q).split()
        if terms:
            rows = [p for p in rows if all(t in p["_search"] for t in terms)]
        rows.sort(key=lambda p: p["created_at"], reverse=True)
        pages = max(1, math.ceil(len(rows) / PER_PAGE))
        page = min(max(page, 1), pages)
        items = [product_to_dto(self.store, p) for p in rows[(page - 1) * PER_PAGE: page * PER_PAGE]]
        return SellerProductsPage(items=items, total=len(rows), page=page, pages=pages)

    def _own(self, seller_id, product_id):
        if not valid_product_id(product_id):
            return None
        p = self.store.products.get(product_id)
        if not p or p["seller_id"] != seller_id or p["status"] == "deleted":
            return None
        return p

    def get_product(self, seller_id, product_id):
        p = self._own(seller_id, product_id)
        return product_to_dto(self.store, p) if p else None

    def leaf_categories(self):
        cats = self.store.categories
        out = []
        for c in sorted(cats.values(), key=lambda c: (c["parent_id"] or 0, c["category_id"])):
            parent = cats.get(c["parent_id"])
            if parent and c["is_active"] and parent["is_active"]:
                out.append((c["category_id"], f"{parent['name']} › {c['name']}"))
        out.sort(key=lambda x: x[1])
        return out

    def _check_category(self, category_id):
        if category_id not in {cid for cid, _ in self.leaf_categories()}:
            raise ValidationError("Choose a valid category.")
        return self.store.categories[category_id]

    def _apply_form(self, record, form, category):
        parent = self.store.categories[category["parent_id"]]
        record.update(
            name=form.name, description=form.description, category_id=form.category_id,
            price=form.price, stock=form.stock, threshold_limit=form.threshold_limit,
            sizes=list(form.sizes), colors=list(form.colors), attributes=dict(form.attributes),
            status="active" if form.visible else "draft",
            color_key=color_key(form.colors[0]),
            kind=KIND_BY_SLUG.get(category["slug"], KIND_BY_TOP.get(parent["slug"], "tshirt")),
            GSI1PK=f"CATEGORY#{category['slug']}", GSI1SK=f"PRICE#{form.price:010.2f}",
        )
        record["_search"] = search_text(self.store, record)

    def create_product(self, seller_id, form):
        category = self._check_category(form.category_id)
        with self.store.lock:
            number = max(int(pid[4:]) for pid in self.store.products) + 1 if self.store.products else 1
            for attempt in range(3):   # another app instance may have taken the id meanwhile
                product_id = f"PRD-{number + attempt:06d}"
                record = {"PK": f"PRODUCT#{product_id}", "SK": "METADATA", "product_id": product_id,
                          "seller_id": seller_id, "created_at": self.store.now().isoformat(),
                          "rating": None, "review_count": 0}
                self._apply_form(record, form, category)
                try:
                    self.store.product_repo.create(record)      # database first, then cache
                    break
                except ProductExists:
                    continue
            else:
                raise ValidationError("Could not generate a product id. Please try again.")
            self.store.products[product_id] = record
            refresh_alert(self.store, record)
        return product_id

    def update_product(self, seller_id, product_id, form):
        category = self._check_category(form.category_id)
        with self.store.lock:
            record = self._own(seller_id, product_id)
            if not record:
                raise ValidationError("Product not found.")
            updated = dict(record)
            self._apply_form(updated, form, category)
            self.store.product_repo.save(updated)              # database first, then cache
            self.store.products[product_id] = updated
            refresh_alert(self.store, updated)

    def delete_product(self, seller_id, product_id):
        with self.store.lock:
            record = self._own(seller_id, product_id)
            if not record:
                raise ValidationError("Product not found.")
            deleted = dict(record, status="deleted")
            self.store.product_repo.save(deleted)
            self.store.products[product_id] = deleted
            # Favorites and carts that still point to it simply stop showing it
            # (both lists skip products that are not public).

    # ---------- photos ----------
    @staticmethod
    def prepare_photo(raw):
        return prepare_photo(raw)

    def set_photo(self, seller_id, product_id, data, content_type):
        """`data` must come from prepare_photo (re-encoded, metadata removed)."""
        with self.store.lock:
            if not self._own(seller_id, product_id):
                raise ValidationError("Product not found.")
        url = self.store.product_repo.set_image(product_id, data, content_type)   # database first
        with self.store.lock:
            record = self.store.products.get(product_id)
            if record is not None:
                self.store.products[product_id] = dict(record, image_url=url)
        return url

    def remove_photo(self, seller_id, product_id):
        with self.store.lock:
            record = self._own(seller_id, product_id)
            if not record:
                raise ValidationError("Product not found.")
            if not record.get("image_url"):
                return
        self.store.product_repo.remove_image(product_id)
        with self.store.lock:
            record = self.store.products.get(product_id)
            if record is not None:
                updated = dict(record)
                updated.pop("image_url", None)
                self.store.products[product_id] = updated

    @staticmethod
    def dynamo_preview(product):
        """How the item looks in MarketplaceDB (shown in the product form)."""
        preview = {"PK": f"PRODUCT#{product.product_id}", "SK": "METADATA",
                   "GSI1PK": f"CATEGORY#{product.category_slug}",
                   "GSI1SK": f"PRICE#{product.price:010.2f}", "seller_id": product.seller_id,
                   "sizes": list(product.sizes), "colors": list(product.colors),
                   "attributes": product.attributes}
        if product.image_url:
            preview["image_url"] = product.image_url
        return preview
