"""FavItem: a user's favorites as ONE item of DynamoDB MarketplaceDB.

    PK = FAV#<id_user>    SK = FAV    items = {product_id: added_at}
"""
from dataclasses import dataclass, field

from Interfaces.Validators import PRODUCT_ID_RE, USER_ID_RE, ValidationError, clean_text

FAV_PK_PREFIX = "FAV#"
FAV_SK = "FAV"
MAX_FAVORITES = 200


@dataclass(frozen=True)
class FavItem:
    id_user: str
    items: dict = field(default_factory=dict)

    @classmethod
    def from_payload(cls, data):
        if not isinstance(data, dict):
            raise ValidationError("Favorites must be a JSON object.")
        id_user = str(data.get("id_user", ""))
        if not USER_ID_RE.fullmatch(id_user):
            raise ValidationError("id_user is invalid.")
        raw = data.get("items", {})
        if not isinstance(raw, dict) or len(raw) > MAX_FAVORITES:
            raise ValidationError(f"items must be an object with at most {MAX_FAVORITES} products.")
        items = {}
        for product_id, added_at in raw.items():
            if not PRODUCT_ID_RE.fullmatch(str(product_id)) or not isinstance(added_at, str):
                raise ValidationError("items must map product ids to dates.")
            items[product_id] = clean_text(added_at, 32, field="added_at")
        return cls(id_user=id_user, items=items)

    def to_item(self):
        return {"PK": FAV_PK_PREFIX + self.id_user, "SK": FAV_SK, "entity": "FAV",
                "id_user": self.id_user, "items": dict(self.items)}

    @classmethod
    def from_item(cls, item):
        return cls(id_user=item["id_user"], items=dict(item.get("items", {})))

    def to_payload(self):
        return {"id_user": self.id_user, "items": dict(self.items)}
