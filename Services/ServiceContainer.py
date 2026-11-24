"""Builds every Service with its Implementation (dependency injection).

DATA_BACKEND=memory (default) | aws
    Users: local, or USER#... items in DynamoDB MarketplaceDB through the
    Lambda users routes (View/MainViews/Utils/api_client.py).
PRODUCTS_BACKEND=memory (default) | aws
    Products: generated locally, or read from / written to DynamoDB
    MarketplaceDB through the products Lambda (View/MainViews/Utils/product_api.py).
    At startup the whole catalog is read from DynamoDB into the in-memory
    cache; every write (stock, seller edits) goes to DynamoDB first.
FINANCE_BACKEND=memory (default) | mysql
    Orders, items, payments, shipping, invoices and history in MySQL finance_db.
OPERATIONS_BACKEND=memory (default) | postgres
    Categories, sellers, reviews, analytics and alerts in PostgreSQL.
DATA_BACKEND=aws also moves carts and favorites to DynamoDB.
Connection settings are read from the environment / .env file (see .env.example).
"""
import os
import time
from dataclasses import dataclass

from Implementation.AdminServiceImplementation.AdminServiceImplementation import AdminServiceImplementation
from Implementation.AuthServiceImplementation.AuthServiceImplementation import AuthServiceImplementation
from Implementation.CartServiceImplementation.CartServiceImplementation import CartServiceImplementation
from Implementation.DataSource.DemoOrders import seed_demo_orders
from Implementation.DataSource.MemoryStore import MemoryStore
from Implementation.DataSource.Mappers import apply_ratings
from Implementation.DataSource.MySQLFinanceRepository import MySQLFinanceRepository
from Implementation.DataSource.PostgresOperationsRepository import PostgresOperationsRepository
from Implementation.DataSource.ProductRepositories import ApiProductRepository, sync_catalog
from Implementation.DataSource.QuickRepositories import ApiQuickRepository
from Implementation.DataSource.SqlConnection import mysql_from_env, postgres_from_env
from Implementation.DataSource.Seed import seed
from Implementation.FavServiceImplementation.FavServiceImplementation import FavServiceImplementation
from Implementation.MainServiceImplementation.MainServiceImplementation import MainServiceImplementation
from Implementation.PayServiceImplementation.PayServiceImplementation import PayServiceImplementation
from Implementation.SellerServiceImplementation.SellerServiceImplementation import SellerServiceImplementation
from Implementation.UserServiceImplementation.UserRepositories import (ApiUserRepository,
                                                                       MemoryUserRepository)
from Implementation.UserServiceImplementation.UserServiceImplementation import UserServiceImplementation
from Services.AdminService.AdminService import AdminService
from Services.AuthService.AuthService import AuthService
from Services.CartService.CartCount import CartCount
from Services.CartService.CartService import CartService
from Services.FavService.FavService import FavService
from Services.MainService.MainService import MainService
from Services.PayService.PayService import PayService
from Services.SellerService.SellerService import SellerService
from Services.UserService.UserService import UserService


@dataclass
class ServiceContainer:
    store: MemoryStore
    main: MainService
    auth: AuthService
    users: UserService
    cart: CartService
    cart_count: CartCount
    fav: FavService
    pay: PayService
    seller: SellerService
    admin: AdminService


def build_services(product_count=None, backend=None, demo_orders=True,
                   products_backend=None, product_client=None, users_api=None,
                   finance_backend=None, finance_db=None, operations_backend=None, operations_db=None,
                   quick_client=None):
    """Every *_db / *_client argument is only for the automated tests; in normal
    use the connections are created from the environment (.env file)."""
    product_count = product_count or int(os.environ.get("PRODUCT_COUNT", "17000"))
    backend = (backend or os.environ.get("DATA_BACKEND", "memory")).lower()
    products_backend = (products_backend or os.environ.get("PRODUCTS_BACKEND", "memory")).lower()
    finance_backend = (finance_backend or os.environ.get("FINANCE_BACKEND", "memory")).lower()
    operations_backend = (operations_backend or os.environ.get("OPERATIONS_BACKEND", "memory")).lower()
    all_memory = all(b == "memory" for b in (backend, products_backend, finance_backend, operations_backend))

    store = MemoryStore()
    seed(store, product_count=product_count)   # demo data; replaced below by every real database

    # ---- PostgreSQL: categories, sellers, reviews, analytics, alerts ----
    if operations_backend == "postgres":
        store.ops = PostgresOperationsRepository(operations_db or postgres_from_env())
        store.reload_meta(force=True)           # categories + sellers now come from PostgreSQL
        if not store.categories:
            raise RuntimeError("PostgreSQL has no categories yet. Load them first with: "
                               "python scripts/load_to_sql.py")
        # Reviews, sales and alerts are read from PostgreSQL on every request.
        store.reviews.clear()
        store.reviews_by_product.clear()
        store.analytics.clear()
        store.alerts.clear()

    # ---- MySQL finance_db: orders, payments, shipping, invoices, history ----
    if finance_backend == "mysql":
        store.finance = MySQLFinanceRepository(finance_db or mysql_from_env())

    # ---- DynamoDB MarketplaceDB: users, carts, favorites ----
    if backend == "aws":
        if users_api is None:
            from View.MainViews.Utils import api_client as users_api
        user_repo = ApiUserRepository(users_api)
        if quick_client is None:
            from View.MainViews.Utils.quick_api import QuickApiClient
            quick_client = QuickApiClient()
        store.quick = ApiQuickRepository(quick_client)
    else:
        user_repo = MemoryUserRepository(store)
    users_impl = UserServiceImplementation(user_repo, store)
    if backend == "aws":
        store.name_resolver = _cached_names(users_impl)

    # ---- DynamoDB MarketplaceDB: products ----
    if products_backend == "aws":
        if product_client is None:
            from View.MainViews.Utils.product_api import ProductApiClient
            product_client = ProductApiClient()
        store.product_repo = ApiProductRepository(product_client, store)
        sync_catalog(store, store.product_repo)    # the catalog now comes from DynamoDB
    elif not store.ops.is_memory:
        apply_ratings(store)                       # ratings of the local catalog from PostgreSQL

    cart_impl = CartServiceImplementation(store)
    pay_impl = PayServiceImplementation(store, cart_impl)
    # Demo orders only in the all-memory demo: with real databases they would
    # spend real stock and fill the tables on every start.
    if demo_orders and all_memory:
        seed_demo_orders(store, cart_impl, pay_impl)

    return ServiceContainer(
        store=store,
        main=MainService(MainServiceImplementation(store)),
        auth=AuthService(AuthServiceImplementation(store, users_impl)),
        users=UserService(users_impl),
        cart=CartService(cart_impl),
        cart_count=CartCount(cart_impl),
        fav=FavService(FavServiceImplementation(store)),
        pay=PayService(pay_impl),
        seller=SellerService(SellerServiceImplementation(store)),
        admin=AdminService(AdminServiceImplementation(store)),
    )


def _cached_names(users_impl, seconds=600):
    """id_user -> name for reviews and admin lists, without one API call per row."""
    cache = {}

    def resolve(user_id):
        hit = cache.get(user_id)
        if hit and hit[0] > time.monotonic():
            return hit[1]
        user = users_impl.get(user_id)
        name = user.name if user else None
        cache[user_id] = (time.monotonic() + seconds, name)
        return name
    return resolve
