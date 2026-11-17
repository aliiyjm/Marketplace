"""Favorites per user (DynamoDB item FAV#<user>)."""
from Implementation.DataSource.Mappers import is_public, product_to_dto
from Interfaces.FavServiceInterface.FavServiceInterface import FavServiceInterface
from Interfaces.Validators import ValidationError

MAX_FAVORITES = 200


class FavServiceImplementation(FavServiceInterface):
    def __init__(self, store):
        self.store = store

    def toggle(self, user_id, product_id):
        p = self.store.products.get(product_id)
        with self.store.lock:
            favs = self.store.favorites.setdefault(user_id, {})
            if product_id in favs:
                del favs[product_id]
                return False
            if not p or not is_public(self.store, p):
                raise ValidationError("This product is not available.")
            if len(favs) >= MAX_FAVORITES:
                raise ValidationError(f"You can save up to {MAX_FAVORITES} favorites.")
            favs[product_id] = self.store.now().isoformat()
            return True

    def list(self, user_id):
        favs = self.store.favorites.get(user_id, {})
        out = []
        for product_id, _ in sorted(favs.items(), key=lambda kv: kv[1], reverse=True):
            p = self.store.products.get(product_id)
            if p and is_public(self.store, p):
                out.append(product_to_dto(self.store, p))
        return out

    def ids(self, user_id):
        return set(self.store.favorites.get(user_id, {})) if user_id else set()
