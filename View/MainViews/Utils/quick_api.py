"""HTTP client for the cart and favorites routes of the Lambda (DynamoDB MarketplaceDB).
Same configuration as product_api.py (PRODUCTS_API_URL / API_URL and MARKETPLACE_API_KEY)."""
from View.MainViews.Utils.product_api import ProductApiClient


class QuickApiClient(ProductApiClient):
    def get_cart(self, id_user):
        data = self._call("GET", "/carrito/consultar", params={"id_user": id_user})
        return data.get("lines", [])

    def save_cart(self, id_user, lines, updated_at=""):
        return self._call("PUT", "/carrito/guardar", write=True,
                          json={"id_user": id_user, "lines": lines, "updated_at": updated_at})

    def clear_cart(self, id_user):
        return self._call("DELETE", "/carrito/vaciar", write=True, params={"id_user": id_user})

    def get_favorites(self, id_user):
        data = self._call("GET", "/favoritos/consultar", params={"id_user": id_user})
        return data.get("items", {})

    def save_favorites(self, id_user, items):
        return self._call("PUT", "/favoritos/guardar", write=True, json={"id_user": id_user, "items": items})
