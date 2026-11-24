"""CartCount: number of units in a user cart (header badge). Kept separate
because it runs on every page and must stay cheap."""


import logging

from Interfaces.Validators import ValidationError

log = logging.getLogger("cart")


class CartCount:
    def __init__(self, impl):
        self.impl = impl          # CartServiceInterface

    def for_user(self, user_id):
        if not user_id:
            return 0
        try:
            return self.impl.count(user_id)
        except ValidationError:      # the badge must never break a page
            log.warning("Cart count unavailable for %s", user_id)
            return 0
