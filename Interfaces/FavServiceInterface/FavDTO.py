"""FavDTO: favorites of a user (DynamoDB)."""
from dataclasses import dataclass

from Interfaces.MainServiceInterface.MainDTO import valid_product_id
from Interfaces.Validators import ValidationError


@dataclass(frozen=True)
class FavoriteForm:
    product_id: str

    @classmethod
    def from_form(cls, form):
        product_id = form.get("product_id", "")
        if not valid_product_id(product_id):
            raise ValidationError("Unknown product.")
        return cls(product_id=product_id)

