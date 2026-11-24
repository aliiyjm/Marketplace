"""In-memory data source that mirrors the database diagram.

It is also the read CACHE when real databases are switched on: products
(DynamoDB), categories and sellers (PostgreSQL) are loaded here because the
catalog search filters 17,000 products on every request. Everything else is
read through the repositories (self.finance, self.ops, self.quick,
self.product_repo), which are memory or database implementations.

Each attribute is one table of the diagram so that, when the Lambdas / SQL
connections are ready, only the Implementation layer has to change:

    DynamoDB  MarketplaceDB (single table): users, products, carts, favorites
    MySQL     orders, order_items, status_history, payments, shipping, invoices
    Postgres  categories, sellers, reviews, analytics, alerts

`lock` plays the role of a database transaction: multi-table writes (checkout,
status changes) run while holding it, after every validation has passed.
"""
import logging
import threading
import time
from collections import defaultdict
from datetime import datetime

log = logging.getLogger("store")


class MemoryStore:
    def __init__(self):
        self.lock = threading.RLock()
        self._clock = None
        # --- DynamoDB: MarketplaceDB ---
        self.users = {}             # id_user -> record
        self.products = {}          # product_id -> record (dynamic JSON attributes)
        self.carts = {}             # id_user -> {line_id: {product_id, size, color, qty}}
        self.favorites = {}         # id_user -> {product_id: added_at}
        # --- PostgreSQL ---
        self.categories = {}        # category_id -> record
        self.sellers = {}           # seller_id -> record
        self.reviews = {}           # review_id -> record
        self.reviews_by_product = defaultdict(list)
        self.analytics = []         # sale records
        self.alerts = {}            # alert_id -> record
        # --- MySQL ---
        self.orders = {}            # order_id -> record
        self.order_items = defaultdict(list)    # order_id -> [item]
        self.status_history = defaultdict(list) # order_id -> [change]
        self.payments = {}          # order_id -> record
        self.shipping = {}          # order_id -> record
        self.invoices = {}          # order_id -> record
        self._counters = defaultdict(int)
        # Repositories: memory by default; ServiceContainer swaps in the real databases.
        from Implementation.DataSource.MemoryRepositories import (MemoryFinanceRepository,
                                                                  MemoryOperationsRepository,
                                                                  MemoryQuickRepository)
        from Implementation.DataSource.ProductRepositories import MemoryProductRepository
        self.product_repo = MemoryProductRepository(self)   # products  (DynamoDB)
        self.quick = MemoryQuickRepository(self)            # carts, favorites (DynamoDB)
        self.ops = MemoryOperationsRepository(self)         # PostgreSQL tables
        self.finance = MemoryFinanceRepository(self)        # MySQL finance_db
        self.meta_ttl = 60          # seconds between reloads of categories/sellers
        self._meta_loaded_at = 0.0
        self.name_resolver = None   # id_user -> name (users may live in DynamoDB)

    # ---- helpers ----
    def next_id(self, table, start=1):
        with self.lock:
            if self._counters[table] < start - 1:
                self._counters[table] = start - 1
            self._counters[table] += 1
            return self._counters[table]

    def now(self):
        return self._clock() if self._clock else datetime.now().replace(microsecond=0)

    def set_clock(self, clock):
        """Tests and the demo seed use a fixed clock; None goes back to real time."""
        self._clock = clock

    def category_by_slug(self, slug):
        for cat in self.categories.values():
            if cat["slug"] == slug:
                return cat
        return None

    def seller_by_user(self, user_id):
        for seller in self.sellers.values():
            if seller["user_id"] == user_id:
                return seller
        return None

    def user_name(self, user_id):
        if self.name_resolver:
            try:
                name = self.name_resolver(user_id)
                if name:
                    return name
            except Exception:   # a name is never worth breaking a page
                log.warning("Could not resolve the name of %s", user_id)
        return self.users.get(user_id, {}).get("name") or "Customer"

    def reload_meta(self, force=False):
        """Reload categories and sellers from PostgreSQL into the cache (every
        `meta_ttl` seconds, or right away after a write). Memory mode: nothing to do."""
        if self.ops.is_memory:
            return
        now = time.monotonic()
        if not force and now - self._meta_loaded_at < self.meta_ttl:
            return
        from Interfaces.DataInterfaces.RepositoryErrors import DatabaseUnavailable
        try:
            categories = {c["category_id"]: c for c in self.ops.list_categories()}
            sellers = {x["seller_id"]: x for x in self.ops.list_sellers()}
        except DatabaseUnavailable:
            if force:
                raise
            log.warning("PostgreSQL unavailable: keeping the cached categories and sellers")
            self._meta_loaded_at = now          # do not retry on every request
            return
        names = lambda cats, sels: ({k: (c["name"], c.get("parent_id")) for k, c in cats.items()},  # noqa: E731
                                    {k: x["store_name"] for k, x in sels.items()})
        with self.lock:
            changed = names(categories, sellers) != names(self.categories, self.sellers)
            self.categories, self.sellers = categories, sellers
            self._meta_loaded_at = now
            if changed:
                from Implementation.DataSource.Mappers import search_text
                for record in self.products.values():
                    record["_search"] = search_text(self, record)
