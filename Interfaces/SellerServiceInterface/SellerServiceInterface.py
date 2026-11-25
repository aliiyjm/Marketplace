"""Contract for the seller center. Every method receives seller_id from the
session and only touches that seller's products (no IDOR)."""
from abc import ABC, abstractmethod


class SellerServiceInterface(ABC):
    @abstractmethod
    def seller_for_user(self, user_id):
        """seller_id linked to the user, or None."""

    @abstractmethod
    def dashboard(self, seller_id, days=30):
        """DashboardDTO from POSTGRES_ANALYTICS and POSTGRES_ALERTS."""

    @abstractmethod
    def list_products(self, seller_id, q="", status="", page=1):
        """SellerProductsPage (includes drafts, excludes deleted)."""

    @abstractmethod
    def get_product(self, seller_id, product_id):
        """ProductDTO if it belongs to the seller, else None."""

    @abstractmethod
    def create_product(self, seller_id, form):
        """ProductForm -> new product_id."""

    @abstractmethod
    def update_product(self, seller_id, product_id, form):
        """ProductForm -> None. Raises ValidationError if not found/not owned."""

    @abstractmethod
    def delete_product(self, seller_id, product_id):
        """Soft delete: hidden from the catalog, past orders still resolve it."""

    @abstractmethod
    def leaf_categories(self):
        """[(category_id, 'Parent › Child')] that products can use."""

    @abstractmethod
    def prepare_photo(self, raw):
        """Untrusted upload bytes -> (clean JPEG bytes, content_type). Raises ValidationError."""

    @abstractmethod
    def set_photo(self, seller_id, product_id, data, content_type):
        """Store the photo of an OWN product (output of prepare_photo). Returns its URL."""

    @abstractmethod
    def remove_photo(self, seller_id, product_id):
        """Delete the photo of an OWN product (back to the SVG silhouette)."""
