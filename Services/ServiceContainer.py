"""Builds every Service with its Implementation (dependency injection).

DATA_BACKEND=memory (default): everything runs on the local MemoryStore.
DATA_BACKEND=aws: users go through the existing API Gateway/Lambda/DynamoDB
CRUD (View/MainViews/Utils/api_client.py); the rest stays local until the
other Lambdas exist.
"""
import os
from dataclasses import dataclass

from Implementation.AdminServiceImplementation.AdminServiceImplementation import AdminServiceImplementation
from Implementation.AuthServiceImplementation.AuthServiceImplementation import AuthServiceImplementation
from Implementation.CartServiceImplementation.CartServiceImplementation import CartServiceImplementation
from Implementation.DataSource.DemoOrders import seed_demo_orders
from Implementation.DataSource.MemoryStore import MemoryStore
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


def build_services(product_count=None, backend=None, demo_orders=True):
    product_count = product_count or int(os.environ.get("PRODUCT_COUNT", "17000"))
    backend = (backend or os.environ.get("DATA_BACKEND", "memory")).lower()

    store = MemoryStore()
    seed(store, product_count=product_count)

    if backend == "aws":
        from View.MainViews.Utils import api_client
        user_repo = ApiUserRepository(api_client)
    else:
        user_repo = MemoryUserRepository(store)

    users_impl = UserServiceImplementation(user_repo, store)
    cart_impl = CartServiceImplementation(store)
    pay_impl = PayServiceImplementation(store, cart_impl)
    if demo_orders:
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
