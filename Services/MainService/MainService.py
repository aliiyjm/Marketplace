"""MainService: entry point of the main home page (catalog and product page).

Services receive raw input (query string / form), turn it into DTOs and call
the Implementation through its Interface. The View only talks to Services.
"""
from Interfaces.MainServiceInterface.MainDTO import (CatalogQuery, ProductPageDTO,
                                                     ReviewForm)


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
        return ProductPageDTO(
            product=product,
            seller=self.impl.get_seller(product.seller_id),
            reviews=self.impl.reviews(product_id),
            rating=self.impl.rating_summary(product_id),
            related=self.impl.related(product),
            can_review=self.impl.can_review(user_id, product_id) if user_id else False,
        )

    def add_review(self, user_id, product_id, form_data):
        return self.impl.add_review(user_id, product_id, ReviewForm.from_form(form_data))
