"""Contract for CartService."""
from abc import ABC, abstractmethod


class CartServiceInterface(ABC):
    @abstractmethod
    def get_cart(self, user_id):
        """CartDTO with current prices and stock."""

    @abstractmethod
    def add(self, user_id, form):
        """AddToCartForm. Raises ValidationError."""

    @abstractmethod
    def update(self, user_id, line_id, qty):
        """Set quantity (0 removes the line)."""

    @abstractmethod
    def remove(self, user_id, line_id):
        """Remove a line."""

    @abstractmethod
    def count(self, user_id):
        """Number of units (header badge)."""

    @abstractmethod
    def clear(self, user_id):
        """Empty the cart after checkout."""
