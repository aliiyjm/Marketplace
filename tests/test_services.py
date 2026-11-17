"""Business rules tested through the Services layer (no HTTP)."""
from datetime import date
from decimal import Decimal

import pytest

from Implementation.DataSource.MemoryStore import MemoryStore
from Implementation.DataSource.Seed import seed
from Interfaces.PayServiceInterface.PayDTO import CheckoutForm
from Interfaces.SellerServiceInterface.SellerDTO import ProductForm
from Interfaces.Validators import ValidationError
from tests.conftest import ANA, CARLOS_SELLER, some_product

ADDRESS = {"street": "Calle Reforma 10", "city": "Cuernavaca", "state": "Morelos", "zip_code": "62000"}
CARD = {"method": "card", "card_number": "4242 4242 4242 4242", "expiry": "12/30", "cvv": "123"}


def add(services, user, p, qty=1, **over):
    data = {"product_id": p["product_id"], "size": p["sizes"][0], "color": p["colors"][0], "qty": str(qty)}
    data.update(over)
    return services.cart.add(user, data)


# ---------- catalog ----------
def test_full_seed_has_17000_products_and_the_tables_of_the_diagram():
    store = MemoryStore()
    seed(store, product_count=17000)
    assert len(store.products) == 17000
    assert len({p["product_id"] for p in store.products.values()}) == 17000
    assert all(p["PK"].startswith("PRODUCT#") for p in store.products.values())
    assert store.categories and store.sellers and store.reviews and store.analytics


def test_catalog_paginates_24_per_page(services):
    page = services.main.home({})
    assert len(page.items) == 24 and page.total == 1200 and page.pages == 50
    last = services.main.home({"page": "50"})
    assert len(last.items) == 1200 - 49 * 24
    assert services.main.home({"page": "99999"}).page == 50       # clamped
    assert services.main.home({"page": "abc"}).page == 1          # ignored


def test_parent_category_includes_its_children(services):
    page = services.main.home({"cat": "women"})
    assert page.total > 0
    assert {p.parent_category_name for p in page.items} == {"Women"}


def test_price_and_stock_filters(services):
    page = services.main.home({"min": "500", "max": "800", "instock": "1", "sort": "price_asc"})
    prices = [p.price for p in page.items]
    assert prices == sorted(prices)
    assert all(Decimal("500") <= p <= Decimal("800") for p in prices)
    assert all(p.stock > 0 for p in page.items)


def test_inactive_category_hides_products(services):
    services.admin.toggle_category(1)                 # Women off
    page = services.main.home({"cat": "women"})   # inactive filter is ignored...
    assert page.category is None and page.total < 1200   # ...and Women products are gone
    assert all(p.parent_category_name != "Women" for p in page.items)


# ---------- cart ----------
def test_cart_rejects_sizes_and_colors_the_product_does_not_have(services):
    p = some_product(services)
    with pytest.raises(ValidationError):
        add(services, ANA, p, size="XXXL-fake")
    with pytest.raises(ValidationError):
        add(services, ANA, p, color="Gold")


def test_cart_cannot_exceed_stock(services):
    p = some_product(services)
    p["stock"] = 3
    add(services, ANA, p, qty=2)
    with pytest.raises(ValidationError):
        add(services, ANA, p, qty=2)


def test_price_comes_from_the_product_not_from_the_form(services):
    p = some_product(services)
    services.cart.add(ANA, {"product_id": p["product_id"], "size": p["sizes"][0], "color": p["colors"][0],
                            "qty": "1", "price": "0.01"})
    cart = services.cart.view(ANA)
    line = next(l for l in cart.lines if l.product.product_id == p["product_id"])
    assert line.unit_price == p["price"]


# ---------- checkout ----------
def test_place_order_writes_every_table_in_one_go(services):
    services.cart.impl.clear(ANA)
    p = some_product(services)
    stock_before = p["stock"]
    add(services, ANA, p, qty=2)
    order_id = services.pay.place_order(ANA, {**ADDRESS, **CARD})
    order = services.pay.my_order(ANA, order_id)
    assert order.status == "Paid"
    assert order.subtotal + order.tax_amount == order.total_amount == p["price"] * 2
    assert p["stock"] == stock_before - 2
    assert [h.new_status for h in order.history] == ["Pending", "Paid"]
    assert order.invoice is not None and order.shipping.zip_code == "62000"
    assert services.cart_count.for_user(ANA) == 0


def test_card_number_is_never_stored(services):
    services.cart.impl.clear(ANA)
    add(services, ANA, some_product(services))
    order_id = services.pay.place_order(ANA, {**ADDRESS, **CARD})
    payment = services.store.payments[order_id]
    assert payment["card_last4"] == "4242"
    assert "4242424242424242" not in repr(services.store.__dict__)


def test_not_enough_stock_creates_nothing(services):
    services.cart.impl.clear(ANA)
    p = some_product(services)
    add(services, ANA, p, qty=2)
    p["stock"] = 1                                     # someone else bought it meanwhile
    orders_before = len(services.store.orders)
    with pytest.raises(ValidationError):
        services.pay.place_order(ANA, {**ADDRESS, **CARD})
    assert len(services.store.orders) == orders_before
    assert p["stock"] == 1


def test_checkout_form_validation():
    with pytest.raises(ValidationError) as err:
        CheckoutForm.from_form({**ADDRESS, "method": "card", "card_number": "4242 4242 4242 4241",
                                "expiry": "01/20", "cvv": "12"}, today=date(2026, 11, 16))
    text = " ".join(err.value.errors)
    assert "card number" in text and "expired" in text and "CVV" in text
    with pytest.raises(ValidationError):
        CheckoutForm.from_form({**ADDRESS, "zip_code": "123", "method": "cash"})
    assert CheckoutForm.from_form({**ADDRESS, "method": "spei"}).card_last4 == ""


def test_status_transitions_and_cancel_returns_stock(services):
    services.cart.impl.clear(ANA)
    p = some_product(services)
    add(services, ANA, p, qty=2)
    order_id = services.pay.place_order(ANA, {**ADDRESS, "method": "cash"})
    stock_after_buy = p["stock"]
    assert services.pay.my_order(ANA, order_id).status == "Pending"
    with pytest.raises(ValidationError):
        services.pay.change_status(order_id, {"new_status": "Delivered"})   # not allowed from Pending
    services.pay.change_status(order_id, {"new_status": "Cancelled"})
    assert p["stock"] == stock_after_buy + 2
    assert services.store.payments[order_id]["status"] == "Cancelled"


def test_buyer_cannot_read_someone_elses_order(services):
    order_id = max(services.store.orders)
    assert services.pay.my_order("u-buyer0001", order_id) is None
    assert services.pay.admin_order(order_id) is not None


# ---------- reviews ----------
def test_only_buyers_can_review_and_only_once(services):
    p = some_product(services)
    with pytest.raises(ValidationError):
        services.main.add_review(ANA, p["product_id"], {"rating": "5", "comment": "Great"})
    services.cart.impl.clear(ANA)
    add(services, ANA, p)
    services.pay.place_order(ANA, {**ADDRESS, **CARD})
    services.main.add_review(ANA, p["product_id"], {"rating": "4", "comment": "Nice fit"})
    with pytest.raises(ValidationError):
        services.main.add_review(ANA, p["product_id"], {"rating": "5", "comment": "Again"})
    assert p["review_count"] >= 1 and p["rating"] is not None


# ---------- seller ----------
def product_form(**over):
    data = {"name": "Linen Shirt", "description": "Fresh", "category_id": "22", "price": "599.00",
            "stock": "12", "threshold_limit": "5", "sizes": "S, M, L", "colors": "White, Navy",
            "visible": "1", "attr_key": ["Material", "Fit"], "attr_value": ["Linen", "Relaxed"]}
    data.update(over)
    return data


class MultiDict(dict):
    def getlist(self, key):
        value = self.get(key, [])
        return value if isinstance(value, list) else [value]


def test_seller_creates_product_with_flexible_attributes(services):
    pid = services.seller.create(CARLOS_SELLER, MultiDict(product_form()))
    p = services.store.products[pid]
    assert p["seller_id"] == CARLOS_SELLER and p["attributes"] == {"Material": "Linen", "Fit": "Relaxed"}
    assert services.main.product_page(pid) is not None


def test_seller_id_in_the_form_is_ignored(services):
    pid = services.seller.create(CARLOS_SELLER, MultiDict(product_form(seller_id="SEL-0002")))
    assert services.store.products[pid]["seller_id"] == CARLOS_SELLER


def test_seller_cannot_touch_another_sellers_product(services):
    other = some_product(services, seller_id="SEL-0005")
    assert services.seller.product(CARLOS_SELLER, other["product_id"]) is None
    with pytest.raises(ValidationError):
        services.seller.update(CARLOS_SELLER, other["product_id"], MultiDict(product_form()))
    with pytest.raises(ValidationError):
        services.seller.delete(CARLOS_SELLER, other["product_id"])
    assert other["status"] == "active"


def test_product_form_rejects_bad_attributes():
    with pytest.raises(ValidationError):
        ProductForm.from_form(MultiDict(product_form(attr_key=["<script>"], attr_value=["x"])))
    with pytest.raises(ValidationError):
        ProductForm.from_form(MultiDict(product_form(attr_key=["Sizes"], attr_value=["XL"])))
    with pytest.raises(ValidationError):
        ProductForm.from_form(MultiDict(product_form(price="-5")))


def test_low_stock_opens_an_alert_and_restock_resolves_it(services):
    pid = services.seller.create(CARLOS_SELLER, MultiDict(product_form(stock="2", threshold_limit="10")))
    alerts = [a for a in services.store.alerts.values() if a["product_id"] == pid]
    assert alerts and alerts[0]["alert_status"] == "Open"
    services.seller.update(CARLOS_SELLER, pid, MultiDict(product_form(stock="40", threshold_limit="10")))
    assert alerts[0]["alert_status"] == "Resolved"


def test_dashboard_has_kpis_and_14_days(services):
    d = services.seller.dashboard(CARLOS_SELLER)
    assert len(d.kpis) == 4 and len(d.daily) == 14
    assert all(x.height % 5 == 0 and 0 <= x.height <= 100 for x in d.daily)


# ---------- auth ----------
def test_password_is_hashed(services):
    record = services.store.users[ANA]
    assert "Demo1234" not in record["password_hash"]
    assert record["password_hash"].startswith(("scrypt:", "pbkdf2:"))


def test_login_lockout_after_five_failures(services):
    for _ in range(5):
        with pytest.raises(ValidationError) as err:
            services.auth.sign_in({"email": "ana@example.com", "password": "wrong"})
        assert "incorrect" in str(err.value)
    with pytest.raises(ValidationError) as err:
        services.auth.sign_in({"email": "ana@example.com", "password": "Demo1234"})
    assert "Too many" in str(err.value)


def test_unknown_email_gets_the_same_message(services):
    with pytest.raises(ValidationError) as err:
        services.auth.sign_in({"email": "nobody@example.com", "password": "Whatever1"})
    assert str(err.value) == "Email or password is incorrect."


def test_register_seller_creates_store_profile(services):
    user = services.auth.register({"name": "Lucía Ramírez", "email": "lucia@shop.mx", "password": "Secret123",
                                   "confirm": "Secret123", "role": "seller", "store_name": "Lucía Moda",
                                   "tax_id": "LURA900101AB1", "terms": "1"})
    assert user.is_seller
    with pytest.raises(ValidationError):   # email already used
        services.auth.register({"name": "X", "email": "lucia@shop.mx", "password": "Secret123",
                                "confirm": "Secret123", "terms": "1"})


def test_register_password_policy(services):
    with pytest.raises(ValidationError) as err:
        services.auth.register({"name": "A", "email": "a@b.co", "password": "short", "confirm": "other", "terms": "1"})
    assert len(err.value.errors) >= 3
