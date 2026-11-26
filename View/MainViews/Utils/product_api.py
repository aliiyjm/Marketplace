"""HTTP client for the products Lambda (API Gateway -> ProductService -> DynamoDB).

Configuration (environment variables):
    PRODUCTS_API_URL      API Gateway invoke URL (defaults to API_URL of api_client.py)
    MARKETPLACE_API_KEY   same value as the Lambda's MARKETPLACE_API_KEY; needed for writes
"""
import base64
import logging
import os

import requests

log = logging.getLogger("product_api")
TIMEOUT = 15   # the first page of a scan can take a few seconds

ROUTE_QUERY = "/productos/consultar"
ROUTE_ADD = "/productos/agregar"
ROUTE_UPDATE = "/productos/modificar"
ROUTE_DELETE = "/productos/eliminar"
ROUTE_STOCK = "/productos/stock"
ROUTE_LOAD = "/productos/cargar"
ROUTE_IMAGE = "/productos/imagen"
ROUTE_RESERVE = "/productos/reservar"
ROUTE_RELEASE = "/productos/liberar"
IMAGE_TIMEOUT = 30   # same as the Lambda timeout: S3 upload + DynamoDB write


class ProductApiError(Exception):
    """Message safe to show; details only in the server log."""

    def __init__(self, message, status=None, code=None):
        super().__init__(message)
        self.status = status
        self.code = code


def default_base_url():
    from View.MainViews.Utils import api_client
    return (os.environ.get("PRODUCTS_API_URL") or api_client.API_URL).rstrip("/")


class ProductApiClient:
    Error = ProductApiError        # lets the Implementation layer catch it without importing the View

    def __init__(self, base_url=None, api_key=None, http=None):
        self.base_url = (base_url or default_base_url()).rstrip("/")
        self.api_key = api_key if api_key is not None else os.environ.get("MARKETPLACE_API_KEY", "")
        self.http = http or requests            # injectable for tests

    def _call(self, method, route, *, write=False, timeout=TIMEOUT, **kwargs):
        # The key goes on every call: writes need it, and reads with it also
        # return drafts (seller center). Without it the Lambda only shows active items.
        headers = {"x-api-key": self.api_key} if self.api_key else {}
        try:
            resp = self.http.request(method, self.base_url + route, headers=headers, timeout=timeout, **kwargs)
        except requests.RequestException:
            raise ProductApiError("Could not connect to the product database.") from None
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if resp.status_code >= 400:
            log.warning("%s %s -> %s %s", method, route, resp.status_code, str(data)[:200])
            message = data.get("error") if isinstance(data, dict) else None
            if resp.status_code in (401, 403):
                message = "The product API rejected the key. Check MARKETPLACE_API_KEY."
            elif resp.status_code == 503 or resp.status_code >= 500 or not message:
                message = f"The product database answered with an error (HTTP {resp.status_code})."
            raise ProductApiError(message, resp.status_code, data.get("code") if isinstance(data, dict) else None)
        return data

    # ---------- reads ----------
    def get(self, product_id):
        try:
            return self._call("GET", ROUTE_QUERY, params={"product_id": product_id})
        except ProductApiError as e:
            if e.status == 404:
                return None
            raise

    def list_page(self, cursor=None, limit=1000, category=None):
        params = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        if category:
            params["category"] = category
        data = self._call("GET", ROUTE_QUERY, params=params)
        return data.get("items", []), data.get("next_cursor")

    def list_all(self, page_size=1000):
        cursor = None
        while True:
            items, cursor = self.list_page(cursor, page_size)
            yield from items
            if not cursor:
                break

    # ---------- writes (need the API key) ----------
    def create(self, payload):
        return self._call("POST", ROUTE_ADD, write=True, json=payload)

    def update(self, payload):
        return self._call("PUT", ROUTE_UPDATE, write=True, json=payload)

    def delete(self, product_id):
        return self._call("DELETE", ROUTE_DELETE, write=True, params={"product_id": product_id})

    def adjust_stock(self, product_id, delta):
        return self._call("POST", ROUTE_STOCK, write=True, json={"product_id": product_id, "delta": delta})["stock"]

    def load(self, payloads):
        return self._call("POST", ROUTE_LOAD, write=True, json={"items": payloads})["written"]

    def upload_image(self, product_id, data, content_type):
        """Send an already re-encoded photo; the Lambda stores it in S3. Returns its URL."""
        body = {"product_id": product_id, "content_type": content_type,
                "data": base64.b64encode(data).decode("ascii")}
        return self._call("POST", ROUTE_IMAGE, write=True, timeout=IMAGE_TIMEOUT, json=body)["image_url"]

    def reserve(self, order_id, items):
        """Atomic stock discount of a whole order. Returns {product_id: new_stock}."""
        return self._call("POST", ROUTE_RESERVE, write=True, json={"order_id": str(order_id), "items": items})["stock"]

    def release(self, order_id):
        """Atomic, once-only stock return of a cancelled order: {released, stock}."""
        return self._call("POST", ROUTE_RELEASE, write=True, json={"order_id": str(order_id)})

    def delete_image(self, product_id):
        return self._call("DELETE", ROUTE_IMAGE, write=True, timeout=IMAGE_TIMEOUT, params={"product_id": product_id})
