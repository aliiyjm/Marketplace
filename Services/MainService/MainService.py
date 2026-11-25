"""MainService: entry point of the main home page (catalog and product page).

Services receive raw input (query string / form), turn it into DTOs and call
the Implementation through its Interface. The View only talks to Services.
"""
import logging

from Interfaces.MainServiceInterface.MainDTO import (CatalogQuery, ProductPageDTO,
                                                     ReviewForm)
from Interfaces.Validators import ValidationError

log = logging.getLogger("main")


class MainService:
    def __init__(self, impl):
        self.impl = impl          # MainServiceInterface

    def home(self, args):
        return self.impl.search(CatalogQuery.from_args(args))

    def categories(self):
        return self.impl.category_tree()

    def product_page(self, product_id, user_id=None):
        product = self.impl.get_product(product_id)
        if product is None:
            return None
        try:
            can_review = self.impl.can_review(user_id, product_id) if user_id else False
        except ValidationError:      # orders database down: the page still works without the form
            log.warning("Could not check purchases of %s", user_id)
            can_review = False
        return ProductPageDTO(
            product=product,
            seller=self.impl.get_seller(product.seller_id),
            reviews=self.impl.reviews(product_id),
            rating=self.impl.rating_summary(product_id),
            related=self.impl.related(product),
            can_review=can_review,
        )

    def add_review(self, user_id, product_id, form_data):
        return self.impl.add_review(user_id, product_id, ReviewForm.from_form(form_data))

    def product_photo(self, product_id, name):
        """Photo stored in memory (local mode). With AWS the photos come from S3."""
        return self.impl.product_photo(f"products/{product_id}/{name}")
