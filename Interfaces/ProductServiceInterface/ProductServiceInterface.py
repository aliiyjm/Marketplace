"""Contracts for products.

ProductServiceInterface      -> what the Lambda does against DynamoDB
                                (ProductServiceImplementation, runs in AWS).
ProductRepositoryInterface   -> what the Flask app needs to keep its catalog in
                                sync with the database (memory or API Gateway).
"""
from abc import ABC, abstractmethod

from Interfaces.Validators import ValidationError


class ProductStoreError(ValidationError):
    """The product database could not complete the operation (message is safe to show)."""


class OutOfStock(ProductStoreError):
    """Not enough stock: someone else bought the last units."""


class ProductNotFound(ProductStoreError):
    """The product does not exist in the database."""


class ProductExists(ProductStoreError):
    """A product with that id already exists."""


class ProductServiceInterface(ABC):
    @abstractmethod
    def get(self, product_id):
        """ProductItem or None."""

    @abstractmethod
    def list_page(self, cursor=None, limit=1000, category_slug=None):
        """(items, next_cursor). With category_slug it queries the GSI1 index."""

    @abstractmethod
    def create(self, item):
        """Insert. Raises ProductExists if the id is taken."""

    @abstractmethod
    def replace(self, item):
        """Overwrite an existing product. Raises ProductNotFound."""

    @abstractmethod
    def soft_delete(self, product_id):
        """status = deleted (orders keep pointing to it). Raises ProductNotFound."""

    @abstractmethod
    def adjust_stock(self, product_id, delta):
        """Atomic stock += delta; never below zero. Returns the new stock.
        Raises OutOfStock / ProductNotFound."""

    @abstractmethod
    def batch_load(self, items):
        """Bulk insert/overwrite (initial load). Returns how many were written."""


class ProductRepositoryInterface(ABC):
    """Persistence used by the Flask Implementation classes. The in-memory
    store is the read cache; every write goes here first."""

    @abstractmethod
    def load_all(self):
        """Iterate over every product record in the database."""

    @abstractmethod
    def get(self, product_id):
        """Fresh record from the database, or None (memory backend: None)."""

    @abstractmethod
    def create(self, record):
        """Persist a new product record."""

    @abstractmethod
    def save(self, record):
        """Persist an updated product record (also used for soft delete)."""

    @abstractmethod
    def adjust_stock(self, product_id, delta):
        """Atomic stock change. Returns the new stock or raises OutOfStock."""
