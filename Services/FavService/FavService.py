"""FavService: user favorite products."""
import logging

from Interfaces.FavServiceInterface.FavDTO import FavoriteForm
from Interfaces.Validators import ValidationError

log = logging.getLogger("favorites")


class FavService:
    def __init__(self, impl):
        self.impl = impl          # FavServiceInterface

    def toggle(self, user_id, form_data):
        return self.impl.toggle(user_id, FavoriteForm.from_form(form_data).product_id)

    def list(self, user_id):
        return self.impl.list(user_id)

    def ids(self, user_id):
        try:
            return self.impl.ids(user_id)
        except ValidationError:      # the hearts must never break a page
            log.warning("Favorites unavailable for %s", user_id)
            return set()
