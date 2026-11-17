"""CartCount: number of units in a user cart (header badge). Kept separate
because it runs on every page and must stay cheap."""


class CartCount:
    def __init__(self, impl):
        self.impl = impl          # CartServiceInterface

    def for_user(self, user_id):
        return self.impl.count(user_id) if user_id else 0
