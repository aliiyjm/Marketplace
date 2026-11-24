"""Carts and favorites in DynamoDB MarketplaceDB through the Lambda.

Every page shows the cart badge and the favorite hearts, so reads are cached
for a few seconds per user; any write from this app updates the cache at once.
"""
import copy
import time

from Interfaces.DataInterfaces.QuickRepositoryInterface import QuickRepositoryInterface
from Interfaces.DataInterfaces.RepositoryErrors import DatabaseUnavailable

CACHE_SECONDS = 10


class ApiQuickRepository(QuickRepositoryInterface):
    def __init__(self, client, clock=time.monotonic):
        self.client = client            # View/MainViews/Utils/quick_api.QuickApiClient
        self.clock = clock
        self._carts = {}                # id_user -> (expires, lines)
        self._favs = {}

    def _call(self, fn, *args):
        try:
            return fn(*args)
        except self.client.Error as e:
            raise DatabaseUnavailable(str(e) or "Could not reach DynamoDB.") from None

    def _cached(self, cache, user_id, loader):
        hit = cache.get(user_id)
        if hit and hit[0] > self.clock():
            return copy.deepcopy(hit[1])
        value = loader()
        cache[user_id] = (self.clock() + CACHE_SECONDS, value)
        return copy.deepcopy(value)

    def get_cart(self, user_id):
        def load():
            lines = self._call(self.client.get_cart, user_id)
            return {l["line_id"]: {"product_id": l["product_id"], "size": l["size"], "color": l["color"],
                                   "qty": int(l["qty"])} for l in lines}
        return self._cached(self._carts, user_id, load)

    def save_cart(self, user_id, lines):
        if lines:
            payload = [dict(line, line_id=line_id) for line_id, line in lines.items()]
            self._call(self.client.save_cart, user_id, payload)
        else:
            self._call(self.client.clear_cart, user_id)
        self._carts[user_id] = (self.clock() + CACHE_SECONDS, copy.deepcopy(lines))

    def get_favorites(self, user_id):
        return self._cached(self._favs, user_id, lambda: dict(self._call(self.client.get_favorites, user_id)))

    def save_favorites(self, user_id, favorites):
        self._call(self.client.save_favorites, user_id, dict(favorites))
        self._favs[user_id] = (self.clock() + CACHE_SECONDS, dict(favorites))
