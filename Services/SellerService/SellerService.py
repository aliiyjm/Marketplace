"""SellerService: seller center (dashboard, products, stock alerts)."""
import json

from Interfaces.SellerServiceInterface.SellerDTO import ProductForm
from Interfaces.Validators import clean_text, parse_int


class SellerService:
    def __init__(self, impl):
        self.impl = impl          # SellerServiceInterface

    def seller_id_for(self, user_id):
        return self.impl.seller_for_user(user_id)

    def dashboard(self, seller_id):
        return self.impl.dashboard(seller_id)

    def products(self, seller_id, args):
        q = clean_text(args.get("q", "")[:80], 80)
        status = args.get("status", "") if args.get("status") in ("active", "draft", "out") else ""
        page = parse_int(args.get("page"), default=1, min_value=1, max_value=100000)
        return self.impl.list_products(seller_id, q, status, page), q, status

    def product(self, seller_id, product_id):
        return self.impl.get_product(seller_id, product_id)

    def create(self, seller_id, form_data):
        return self.impl.create_product(seller_id, ProductForm.from_form(form_data))

    def update(self, seller_id, product_id, form_data):
        self.impl.update_product(seller_id, product_id, ProductForm.from_form(form_data))

    def delete(self, seller_id, product_id):
        self.impl.delete_product(seller_id, product_id)

    def categories(self):
        return self.impl.leaf_categories()

    def dynamo_json(self, product):
        return json.dumps(self.impl.dynamo_preview(product), indent=2, ensure_ascii=False, default=str)
