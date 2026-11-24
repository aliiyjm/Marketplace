"""Contract for the "quick transactions" (DynamoDB MarketplaceDB): carts and favorites.

    cart       {line_id: {"product_id", "size", "color", "qty"}}
    favorites  {product_id: added_at}
"""
from abc import ABC, abstractmethod


class QuickRepositoryInterface(ABC):
    is_memory = False

    @abstractmethod
    def get_cart(self, user_id):
        """A COPY of the user's cart lines (empty dict if none)."""

    @abstractmethod
    def save_cart(self, user_id, lines):
        """Replace the user's cart."""

    @abstractmethod
    def get_favorites(self, user_id):
        """A COPY of the user's favorites (empty dict if none)."""

    @abstractmethod
    def save_favorites(self, user_id, favorites):
        """Replace the user's favorites."""
