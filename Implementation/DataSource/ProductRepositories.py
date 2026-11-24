"""Where product writes go. The in-memory store is the Flask read cache; every
write is persisted through one of these repositories BEFORE the cache changes,
so a failed write never leaves the page showing data that is not in the database.

- MemoryProductRepository: local mode (no database). The cache is the data.
- ApiProductRepository:    API Gateway -> products Lambda -> DynamoDB MarketplaceDB.
"""
import logging

from Implementation.DataSource.Inventory import refresh_alert
from Implementation.DataSource.Mappers import apply_ratings, search_text
from Interfaces.ProductServiceInterface.ProductDTO import ProductItem
from Interfaces.ProductServiceInterface.ProductServiceInterface import (OutOfStock, ProductExists,
                                                                        ProductNotFound,
                                                                        ProductRepositoryInterface,
                                                                        ProductStoreError)
from Interfaces.Validators import ValidationError

log = logging.getLogger("products")


class MemoryProductRepository(ProductRepositoryInterface):
    def __init__(self, store):
        self.store = store

    def load_all(self):
        return list(self.store.products.values())

    def get(self, product_id):
        return self.store.products.get(product_id)

    def create(self, record):
        if record["product_id"] in self.store.products:
            raise ProductExists("That product id already exists.")

    def save(self, record):
        pass

    def adjust_stock(self, product_id, delta):
        p = self.store.products.get(product_id)
        if p is None:
            raise ProductNotFound("Product not found.")
        if p["stock"] + delta < 0:
            raise OutOfStock(f"Only {p['stock']} left of {p['name']}.")
        return p["stock"] + delta


class ApiProductRepository(ProductRepositoryInterface):
    def __init__(self, client, store):
        self.client = client          # View/MainViews/Utils/product_api.ProductApiClient
        self.store = store

    def _payload(self, record):
        category = self.store.categories.get(record["category_id"])
        if not category:
            raise ValidationError("Choose a valid category.")
        return ProductItem.from_record(record, category["slug"]).to_payload()

    def _translate(self, error, default):
        code = getattr(error, "code", None)
        message = str(error) or default
        if code == "OUT_OF_STOCK":
            return OutOfStock(message)
        if code == "NOT_FOUND":
            return ProductNotFound(message)
        if code == "EXISTS":
            return ProductExists(message)
        return ProductStoreError(message if getattr(error, "status", None) == 400 else default)

    def load_all(self):
        try:
            payloads = list(self.client.list_all())
        except self.client.Error as e:   # wrong API_URL / key, Lambda down: a clear message at startup
            raise self._translate(e, f"Could not read the products from DynamoDB: {e}") from None
        for payload in payloads:
            try:
                yield ProductItem.from_payload(payload).to_record()
            except ValidationError as e:     # never let one bad item stop the whole catalog
                log.warning("Skipping invalid product %s: %s", payload.get("product_id"), e)

    def get(self, product_id):
        try:
            payload = self.client.get(product_id)
        except self.client.Error as e:
            raise self._translate(e, "Could not read the product.") from None
        return ProductItem.from_payload(payload).to_record() if payload else None

    def create(self, record):
        try:
            self.client.create(self._payload(record))
        except self.client.Error as e:
            raise self._translate(e, "Could not save the product. Try again.") from None

    def save(self, record):
        try:
            self.client.update(self._payload(record))
        except self.client.Error as e:
            raise self._translate(e, "Could not save the product. Try again.") from None

    def adjust_stock(self, product_id, delta):
        try:
            return int(self.client.adjust_stock(product_id, delta))
        except self.client.Error as e:
            raise self._translate(e, "Could not update the stock. Try again.") from None


def sync_catalog(store, repo):
    """Replace the seeded catalog with what the database has (startup)."""
    loaded = {}
    for record in repo.load_all():
        loaded[record["product_id"]] = record
    if not loaded:
        raise RuntimeError("DynamoDB has no products yet. Load them first with: "
                           "python scripts/load_to_dynamo.py")
    with store.lock:
        store.products = loaded
        for record in loaded.values():
            record["_search"] = search_text(store, record)
        apply_ratings(store)          # one query: every product's average and count
        if store.ops.is_memory:       # with PostgreSQL the alerts are already stored there
            store.alerts.clear()
            for record in loaded.values():
                refresh_alert(store, record)
    return len(loaded)


def adjust_many(store, pairs):
    """Apply several stock changes as one unit: if one fails, undo the ones
    already applied (compensation, since they are separate API calls).
    `pairs` = [(product_id, delta)]. Updates the cache with the database value."""
    repo = store.product_repo
    done = []
    current = None
    try:
        for product_id, delta in pairs:
            current = product_id
            new_stock = repo.adjust_stock(product_id, delta)
            done.append((product_id, delta))
            store.products[product_id]["stock"] = new_stock
    except ProductStoreError:
        try:   # show the real stock of the product that failed on the next page load
            fresh = repo.get(current)
            if fresh is not None and fresh is not store.products.get(current):
                store.products[current]["stock"] = fresh["stock"]
        except ProductStoreError:
            pass
        for product_id, delta in reversed(done):
            try:
                store.products[product_id]["stock"] = repo.adjust_stock(product_id, -delta)
            except ProductStoreError:
                log.error("Could not undo stock change %s %+d: fix it manually", product_id, delta)
        raise
