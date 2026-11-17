"""AdminService: categories management."""
from Interfaces.AdminServiceInterface.AdminDTO import CategoryForm


class AdminService:
    def __init__(self, impl):
        self.impl = impl          # AdminServiceInterface

    def categories(self):
        return self.impl.category_rows()

    def category(self, category_id):
        return self.impl.get_category(category_id)

    def top_categories(self):
        return self.impl.top_categories()

    def create_category(self, form_data):
        return self.impl.create_category(CategoryForm.from_form(form_data))

    def update_category(self, category_id, form_data):
        self.impl.update_category(category_id, CategoryForm.from_form(form_data))

    def toggle_category(self, category_id):
        return self.impl.toggle_category(category_id)
