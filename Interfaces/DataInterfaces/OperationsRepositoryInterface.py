"""Contract for the operational data (PostgreSQL):
categories, sellers, reviews, analytics, alerts.

Categories and sellers are small and are needed to filter 17,000 products,
so the app keeps them in memory (loaded from here, refreshed periodically);
every write still goes here first. Reviews, analytics and alerts are read
from the database on every request.
"""
from abc import ABC, abstractmethod


class OperationsRepositoryInterface(ABC):
    is_memory = False

    # ---------- categories ----------
    @abstractmethod
    def list_categories(self):
        """[{category_id, parent_id, name, slug, is_active}]"""

    @abstractmethod
    def create_category(self, record):
        """Insert (without id). Returns the new category_id. DuplicateKey on slug."""

    @abstractmethod
    def update_category(self, category_id, record):
        """Update name, slug, parent_id, is_active."""

    # ---------- sellers ----------
    @abstractmethod
    def list_sellers(self):
        """[{seller_id, user_id, store_name, tax_id, rating, status, created_at}]"""

    @abstractmethod
    def create_seller(self, record):
        """Insert with a new seller_id (SEL-0001...). Returns the seller_id."""

    @abstractmethod
    def set_seller_status(self, seller_id, status):
        """status: active | suspended"""

    # ---------- reviews ----------
    @abstractmethod
    def reviews_for(self, product_id, limit=5):
        """Newest reviews of a product."""

    @abstractmethod
    def rating_counts(self, product_id):
        """{stars: count} for 1..5."""

    @abstractmethod
    def rating_stats(self):
        """{product_id: (average, count)} for every reviewed product."""

    @abstractmethod
    def has_reviewed(self, buyer_id, product_id):
        """True if that buyer already reviewed the product."""

    @abstractmethod
    def add_review(self, record):
        """Insert a review. Returns review_id. DuplicateKey if already reviewed."""

    # ---------- analytics ----------
    @abstractmethod
    def record_sales(self, rows):
        """Insert sale rows {product_id, category_id, seller_id, quantity_sold, sale_date}."""

    @abstractmethod
    def units_by_day(self, seller_id, since):
        """{date: units} from `since` (datetime.date) until today."""

    @abstractmethod
    def top_products(self, seller_id, since, limit=5):
        """[(product_id, units)] best sellers since `since`."""

    # ---------- alerts ----------
    @abstractmethod
    def open_alert(self, product_id):
        """The open alert of a product, or None."""

    @abstractmethod
    def insert_alert(self, record):
        """Insert {product_id, seller_id, current_stock, threshold_limit, alert_status, updated_at}."""

    @abstractmethod
    def update_alert(self, alert_id, fields):
        """Update current_stock / threshold_limit / alert_status / updated_at."""

    @abstractmethod
    def alerts_for_seller(self, seller_id, limit=6):
        """Open alerts first (lowest stock first), then resolved ones."""

    @abstractmethod
    def count_open_alerts(self, seller_id):
        """Number of open alerts of a seller."""
