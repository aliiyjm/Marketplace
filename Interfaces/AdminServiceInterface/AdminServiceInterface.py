"""Contract for administration (categories). Users have their own UserService."""
from abc import ABC, abstractmethod


class AdminServiceInterface(ABC):
    @abstractmethod
    def category_rows(self):
        """Flat tree (parents followed by children) as CategoryRowDTO."""

    @abstractmethod
    def get_category(self, category_id):
        """CategoryRowDTO or None."""

    @abstractmethod
    def create_category(self, form):
        """CategoryForm -> category_id. Slug must be unique, max depth 2."""

    @abstractmethod
    def update_category(self, category_id, form):
        """CategoryForm -> None."""

    @abstractmethod
    def toggle_category(self, category_id):
        """Flip is_active. Returns the new value."""
