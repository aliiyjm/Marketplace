"""Contract for the home page / catalog. MainServiceImplementation fulfils it."""
from abc import ABC, abstractmethod


class MainServiceInterface(ABC):
    @abstractmethod
    def category_tree(self):
        """Active top-level categories (CategoryDTO) with their children."""

    @abstractmethod
    def search(self, query):
        """CatalogQuery -> CatalogPage (24 products per page)."""

    @abstractmethod
    def get_product(self, product_id):
        """ProductDTO of an active product, or None."""

    @abstractmethod
    def get_seller(self, seller_id):
        """SellerSummaryDTO or None."""

    @abstractmethod
    def reviews(self, product_id, limit=5):
        """Most recent ReviewDTOs of a product."""

    @abstractmethod
    def rating_summary(self, product_id):
        """RatingSummaryDTO with the 5..1 star distribution."""

    @abstractmethod
    def can_review(self, user_id, product_id):
        """True when the user bought the product and has not reviewed it yet."""

    @abstractmethod
    def add_review(self, user_id, product_id, form):
        """Store a ReviewForm. Raises ValidationError when not allowed."""
