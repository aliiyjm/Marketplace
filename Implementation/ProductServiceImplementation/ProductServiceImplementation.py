"""DynamoDB access for products (runs inside the Lambda).

This is the only layer that knows the table. It receives a boto3 Table object
(injected), so tests can pass a fake one.

Stock is changed with a conditional UpdateItem: DynamoDB itself refuses to go
below zero, so two buyers cannot get the last unit at the same time.

Photos: the file goes to the image store (S3) first and only then its URL is
written in the item; if that write fails the new file is deleted again, so the
table never points to a missing photo.
"""
import base64
import binascii
import json
import logging
import secrets

from Interfaces.ProductServiceInterface.ProductDTO import (IMAGE_TYPES, PK_PREFIX, SK_PRODUCT,
                                                           ProductItem, image_key)
from Interfaces.ProductServiceInterface.ProductServiceInterface import (ImagesDisabled, OutOfStock,
                                                                        ProductExists, ProductNotFound,
                                                                        ProductServiceInterface)
from Interfaces.Validators import ValidationError

image_log = logging.getLogger("product_images")

MAX_PAGE = 2000
MAX_STOCK_DELTA = 10000
_CURSOR_KEYS = {"PK", "SK", "GSI1PK", "GSI1SK"}


def is_conditional_failure(error):
    """botocore ClientError with ConditionalCheckFailedException (no botocore import needed)."""
    code = getattr(error, "response", {}).get("Error", {}).get("Code")
    return code == "ConditionalCheckFailedException"


def encode_cursor(key):
    if not key:
        return None
    return base64.urlsafe_b64encode(json.dumps(key, separators=(",", ":")).encode()).decode()


def decode_cursor(cursor):
    if not cursor:
        return None
    try:
        key = json.loads(base64.urlsafe_b64decode(cursor.encode()).decode())
    except (ValueError, binascii.Error, UnicodeDecodeError):
        raise ValidationError("Invalid cursor.") from None
    if (not isinstance(key, dict) or not key or not set(key) <= _CURSOR_KEYS
            or not all(isinstance(v, str) and len(v) <= 200 for v in key.values())):
        raise ValidationError("Invalid cursor.")
    return key


class ProductServiceImplementation(ProductServiceInterface):
    def __init__(self, table, index_name="GSI1", images=None):
        self.table = table
        self.index_name = index_name
        self.images = images          # ProductImageStoreInterface (S3) or None = photos disabled

    @staticmethod
    def _key(product_id):
        return {"PK": PK_PREFIX + product_id, "SK": SK_PRODUCT}

    def get(self, product_id):
        item = self.table.get_item(Key=self._key(product_id)).get("Item")
        return ProductItem.from_item(item) if item else None

    def list_page(self, cursor=None, limit=1000, category_slug=None):
        limit = max(1, min(int(limit), MAX_PAGE))
        start = decode_cursor(cursor)
        if category_slug:
            operation = self.table.query
            kwargs = {"IndexName": self.index_name, "KeyConditionExpression": "GSI1PK = :c",
                      "ExpressionAttributeValues": {":c": f"CATEGORY#{category_slug}"}}
        else:
            operation = self.table.scan
            kwargs = {"FilterExpression": "begins_with(PK, :p) AND SK = :sk",
                      "ExpressionAttributeValues": {":p": PK_PREFIX, ":sk": SK_PRODUCT}}
        items = []
        while True:
            call = dict(kwargs, Limit=limit - len(items))
            if start:
                call["ExclusiveStartKey"] = start
            resp = operation(**call)
            items.extend(resp.get("Items", []))
            start = resp.get("LastEvaluatedKey")
            if not start or len(items) >= limit:
                break
        return [ProductItem.from_item(i) for i in items], encode_cursor(start)

    def create(self, item):
        try:
            self.table.put_item(Item=item.to_item(), ConditionExpression="attribute_not_exists(PK)")
        except Exception as e:
            if is_conditional_failure(e):
                raise ProductExists(f"Product {item.product_id} already exists.") from None
            raise

    def replace(self, item):
        try:
            self.table.put_item(Item=item.to_item(), ConditionExpression="attribute_exists(PK)")
        except Exception as e:
            if is_conditional_failure(e):
                raise ProductNotFound(f"Product {item.product_id} does not exist.") from None
            raise

    def soft_delete(self, product_id):
        try:
            self.table.update_item(Key=self._key(product_id), UpdateExpression="SET #s = :d",
                                   ConditionExpression="attribute_exists(PK)",
                                   ExpressionAttributeNames={"#s": "status"},
                                   ExpressionAttributeValues={":d": "deleted"})
        except Exception as e:
            if is_conditional_failure(e):
                raise ProductNotFound(f"Product {product_id} does not exist.") from None
            raise

    def adjust_stock(self, product_id, delta):
        if not isinstance(delta, int) or isinstance(delta, bool) or delta == 0 or abs(delta) > MAX_STOCK_DELTA:
            raise ValidationError("delta must be a non-zero whole number.")
        kwargs = {"Key": self._key(product_id), "UpdateExpression": "SET stock = stock + :d",
                  "ExpressionAttributeValues": {":d": delta}, "ReturnValues": "UPDATED_NEW"}
        if delta < 0:
            kwargs["ConditionExpression"] = "attribute_exists(PK) AND stock >= :need"
            kwargs["ExpressionAttributeValues"][":need"] = -delta
        else:
            kwargs["ConditionExpression"] = "attribute_exists(PK)"
        try:
            resp = self.table.update_item(**kwargs)
        except Exception as e:
            if not is_conditional_failure(e):
                raise
            current = self.get(product_id)
            if current is None:
                raise ProductNotFound(f"Product {product_id} does not exist.") from None
            raise OutOfStock(f"Only {current.stock} left of {current.name}.") from None
        return int(resp["Attributes"]["stock"])

    def batch_load(self, items):
        with self.table.batch_writer(overwrite_by_pkeys=["PK", "SK"]) as writer:
            for item in items:
                writer.put_item(Item=item.to_item())
        return len(items)

    # ---------- photos ----------
    def _store(self):
        if self.images is None:
            raise ImagesDisabled("Photos are disabled: MARKETPLACE_BUCKET is not configured in the Lambda.")
        return self.images

    def _delete_old(self, url):
        """Best effort: a leftover file only costs storage, never breaks a page."""
        key = self.images.key_of(url) if url else None
        if key:
            try:
                self.images.delete(key)
            except Exception:
                image_log.exception("Could not delete the old photo %s", key)

    def set_image(self, product_id, data, content_type):
        store = self._store()
        if content_type not in IMAGE_TYPES:
            raise ValidationError("Unsupported photo type.")
        current = self.get(product_id)
        if current is None:
            raise ProductNotFound(f"Product {product_id} does not exist.")
        key = image_key(product_id, secrets.token_hex(16), content_type)
        store.put(key, data, content_type)
        url = store.url(key)
        try:
            self.table.update_item(Key=self._key(product_id), UpdateExpression="SET image_url = :u",
                                   ConditionExpression="attribute_exists(PK)",
                                   ExpressionAttributeValues={":u": url})
        except Exception as e:
            store.delete(key)                       # do not leave an orphan file
            if is_conditional_failure(e):
                raise ProductNotFound(f"Product {product_id} does not exist.") from None
            raise
        if current.image_url != url:
            self._delete_old(current.image_url)
        return url

    def remove_image(self, product_id):
        self._store()
        current = self.get(product_id)
        if current is None:
            raise ProductNotFound(f"Product {product_id} does not exist.")
        if not current.image_url:
            return
        try:
            self.table.update_item(Key=self._key(product_id), UpdateExpression="REMOVE image_url",
                                   ConditionExpression="attribute_exists(PK)")
        except Exception as e:
            if is_conditional_failure(e):
                raise ProductNotFound(f"Product {product_id} does not exist.") from None
            raise
        self._delete_old(current.image_url)
