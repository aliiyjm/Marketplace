"""PayService: checkout, orders, payment status and invoices."""
from Interfaces.AdminServiceInterface.AdminDTO import StatusForm
from Interfaces.PayServiceInterface.PayDTO import ORDER_STATUSES, CheckoutForm
from Interfaces.Validators import clean_text


class PayService:
    def __init__(self, impl):
        self.impl = impl          # PayServiceInterface

    def place_order(self, user_id, form_data):
        return self.impl.place_order(user_id, CheckoutForm.from_form(form_data))

    @staticmethod
    def _filters(args):
        status = args.get("status", "")
        q = args.get("q", "")[:20]
        return (status if status in ORDER_STATUSES else None), clean_text(q, 20)

    def my_orders(self, user_id, args):
        status, q = self._filters(args)
        return self.impl.list_orders(user_id, status, q), status, q

    def my_order(self, user_id, order_id):
        return self.impl.get_order(order_id, user_id=user_id)

    def admin_orders(self, args):
        status, q = self._filters(args)
        return self.impl.all_orders(status, q), status, q

    def admin_order(self, order_id):
        return self.impl.get_order(order_id, is_admin=True)

    def change_status(self, order_id, form_data):
        self.impl.change_status(order_id, StatusForm.from_form(form_data).new_status)
