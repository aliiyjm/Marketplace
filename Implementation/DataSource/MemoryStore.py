"""In-memory data source that mirrors the database diagram.

Each attribute is one table of the diagram so that, when the Lambdas / SQL
connections are ready, only the Implementation layer has to change:

    DynamoDB  MarketplaceDB (single table): users, products, carts, favorites
    MySQL     orders, order_items, status_history, payments, shipping, invoices
    Postgres  categories, sellers, reviews, analytics, alerts

`lock` plays the role of a database transaction: multi-table writes (checkout,
status changes) run while holding it, after every validation has passed.
"""
import threading
from collections import defaultdict
from datetime import datetime


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
