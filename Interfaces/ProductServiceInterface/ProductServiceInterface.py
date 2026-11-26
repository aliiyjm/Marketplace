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


class ImagesDisabled(ProductStoreError):
    """Photos are not configured (the Lambda has no MARKETPLACE_BUCKET)."""


class ReservationExists(ProductStoreError):
    """That order number already has a stock reservation (another checkout took it)."""


class ReservationNotFound(ProductStoreError):
    """The order has no stock reservation (orders created before reservations existed)."""


class ProductImageStoreInterface(ABC):
    """Where photo files live (S3 bucket in AWS, a dict in the tests)."""

    @abstractmethod
    def put(self, key, data, content_type):
        """Store the bytes under `key` (products/PRD-000123/<random>.jpg)."""

    @abstractmethod
    def delete(self, key):
        """Remove the file (no error if it does not exist)."""

    @abstractmethod
    def url(self, key):
        """Public URL of the file."""

    @abstractmethod
    def key_of(self, url):
        """Key of a URL from THIS store, or None (never touches other buckets)."""


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

    @abstractmethod
    def set_image(self, product_id, data, content_type):
        """Store an already validated photo and save its URL in the product
        (the previous photo is deleted). Returns the new URL.
        Raises ProductNotFound / ImagesDisabled."""

    @abstractmethod
    def remove_image(self, product_id):
        """Delete the photo of the product (back to the SVG silhouette)."""

    @abstractmethod
    def reserve_stock(self, order_id, quantities):
        """ATOMIC TRANSACTION 1 (TransactWriteItems): discount the stock of every
        product of the order and write the reservation, all or nothing.
        `quantities` = {product_id: qty}. Returns {product_id: new_stock}.
        Raises OutOfStock / ProductNotFound / ReservationExists."""

    @abstractmethod
    def release_stock(self, order_id):
        """ATOMIC TRANSACTION 2 (TransactWriteItems): mark the reservation as
        released and give the stock back, all or nothing and only once.
        Returns (released, {product_id: new_stock}); released=False when it was
        already released. Raises ReservationNotFound."""


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

    @abstractmethod
    def set_image(self, product_id, data, content_type):
        """Persist a photo (bytes already re-encoded and checked). Returns its URL."""

    @abstractmethod
    def remove_image(self, product_id):
        """Delete the photo of the product."""

    @abstractmethod
    def reserve_stock(self, order_id, quantities):
        """Discount the stock of the whole order at once. Returns {product_id: new_stock}."""

    @abstractmethod
    def release_stock(self, order_id):
        """Give back the stock of a cancelled order once. Returns {product_id: new_stock},
        or None when the order has no reservation (older orders)."""

    def read_image(self, key):
        """(content_type, bytes) of a photo served by this app, or None.
        Only the memory backend serves photos itself; S3 serves its own."""
        return None
