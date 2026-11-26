"""Contract for PayService (orders, payments, invoices)."""
from abc import ABC, abstractmethod


class PayServiceInterface(ABC):
    @abstractmethod
    def place_order(self, user_id, form):
        """Turn the cart into an order in ONE transaction (like the stored procedure):
        check stock, create order + items, discount stock, save shipping and payment.
        Returns the order_id. Raises ValidationError."""

    @abstractmethod
    def list_orders(self, user_id, status=None, q=""):
        """Orders of a buyer, newest first."""

    @abstractmethod
    def get_order(self, order_id, user_id=None, is_admin=False):
        """OrderDTO only if it belongs to user_id (or caller is admin). Else None."""

    @abstractmethod
    def change_status(self, order_id, new_status):
        """Apply an allowed transition and write MYSQL_STATUS_HISTORY."""

    @abstractmethod
    def all_orders(self, status=None, q=""):
        """Every order (admin)."""

    @abstractmethod
    def sales_history(self, user_id):
        """BuyerHistoryDTO: consolidated history of the buyer (stored procedure)."""
