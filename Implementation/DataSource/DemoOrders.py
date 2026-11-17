"""Demo order history for ana@example.com, created through the real checkout
logic (so stock, history, invoices and analytics are consistent)."""
from datetime import datetime, timedelta

from Interfaces.CartServiceInterface.CartDTO import AddToCartForm
from Interfaces.PayServiceInterface.PayDTO import CheckoutForm

ANA = "u-ana00001"
ADDRESS = dict(street="Av. Universidad 1001, Int. 4", city="Emiliano Zapata",
               state="Morelos", zip_code="62760")
# (days ago, product numbers, method, status path after placing)
PLAN = [
    (46, [4, 101], "card", ["Shipped", "Delivered"]),
    (30, [13, 240, 907], "card", ["Shipped", "Delivered"]),
    (19, [6], "spei", ["Cancelled"]),
    (9, [25, 331], "card", ["Shipped"]),
    (2, [41], "cash", []),
    (0, [2, 77, 512], "card", []),
]


def seed_demo_orders(store, cart, pay):
    real_now = datetime.now().replace(microsecond=0)
    try:
        for days_ago, numbers, method, path in PLAN:
            moment = [real_now - timedelta(days=days_ago, hours=3)]
            store.set_clock(lambda: moment[0])
            for n in numbers:
                p = store.products.get(f"PRD-{n:06d}")
                if not p or p["stock"] <= 0:
                    continue
                cart.add(ANA, AddToCartForm(p["product_id"], p["sizes"][len(p["sizes"]) // 2],
                                            p["colors"][0], 1 + (n % 2)))
            if cart.count(ANA) == 0:
                continue
            last4 = "4242" if method == "card" else ""
            order_id = pay.place_order(ANA, CheckoutForm(method=method, card_last4=last4, **ADDRESS))
            for step, status in enumerate(path, start=1):
                moment[0] = moment[0] + timedelta(days=step, hours=5)
                pay.change_status(order_id, status)
    finally:
        store.set_clock(None)
