"""Contract for FavService."""
from abc import ABC, abstractmethod


class FavServiceInterface(ABC):
    @abstractmethod
    def toggle(self, user_id, product_id):
        """Add or remove. Returns True if it is now a favorite."""

    @abstractmethod
    def list(self, user_id):
        """ProductDTOs still available, newest first."""

    @abstractmethod
    def ids(self, user_id):
        """Set of favorite product ids (to paint the hearts)."""
