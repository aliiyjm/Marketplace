"""DynamoDB access for the quick transactions (runs inside the Lambda):
one item per user for the cart (CART#) and one for the favorites (FAV#)."""
from Interfaces.CartServiceInterface.CartItem import CART_PK_PREFIX, CART_SK, CartItem
from Interfaces.FavServiceInterface.FavItem import FAV_PK_PREFIX, FAV_SK, FavItem


class CartDynamoImplementation:
    def __init__(self, table):
        self.table = table

    def get(self, id_user):
        item = self.table.get_item(Key={"PK": CART_PK_PREFIX + id_user, "SK": CART_SK}).get("Item")
        return CartItem.from_item(item) if item else CartItem(id_user=id_user, lines=())

    def save(self, cart):
        self.table.put_item(Item=cart.to_item())

    def clear(self, id_user):
        self.table.delete_item(Key={"PK": CART_PK_PREFIX + id_user, "SK": CART_SK})


class FavDynamoImplementation:
    def __init__(self, table):
        self.table = table

    def get(self, id_user):
        item = self.table.get_item(Key={"PK": FAV_PK_PREFIX + id_user, "SK": FAV_SK}).get("Item")
        return FavItem.from_item(item) if item else FavItem(id_user=id_user)

    def save(self, favorites):
        self.table.put_item(Item=favorites.to_item())
