# CRUD_Marketplace · clothing marketplace

Transactional marketplace for clothing, shoes and accessories with a 17,000-product
catalog. Python 3.13 + Flask, layered architecture:

```
Services/        entry points the View calls (MainService, CartService + CartCount,
                 PayService, AuthService, FavService, SellerService, AdminService,
                 UserService) and ServiceContainer.py, which wires everything
Interfaces/      DTOs (validated, whitelisted fields) + abstract contracts
Implementation/  business rules; DataSource/ holds the in-memory tables of the
                 database diagram, the 17,000-product seed and demo orders
View/MainViews/  PY (Flask routes + security), HTML (Jinja templates), CSS, JS, Utils
tests/           automated tests (pytest)
```

## Run it

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/Mac: source .venv/bin/activate)
pip install -r requirements.txt
python View/MainViews/PY/app.py
```

Open http://127.0.0.1:5000

Demo accounts (password `Demo1234`):

| Email | Role |
| --- | --- |
| ana@example.com | buyer (has orders in every status) |
| carlos@urbanthread.mx | seller · Urban Thread (dashboard, alerts) |
| admin@marketplace.mx | admin (users, categories, order status) |

## Automated tests

```bash
python -m pytest -q
```

## Configuration (environment variables)

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATA_BACKEND` | `memory` | `aws` sends the users CRUD to the API Gateway/Lambda/DynamoDB in `View/MainViews/Utils/api_client.py`. Everything else runs on the local data source until its Lambdas exist. |
| `API_URL` | see api_client.py | API Gateway invoke URL (only with `DATA_BACKEND=aws`). |
| `PRODUCT_COUNT` | `17000` | Size of the generated catalog. |
| `SECRET_KEY` | random | Set it so sessions survive restarts. |
| `SESSION_COOKIE_SECURE` | off | Set `1` when served over HTTPS. |
| `FLASK_DEBUG` | off | `1` for auto-reload while developing. Never in production. |

## Where each table of the diagram lives

| Diagram | Code |
| --- | --- |
| DynamoDB MarketplaceDB (users, products with dynamic JSON) | `store.users`, `store.products` (+ carts, favorites) |
| MYSQL_ORDERS / ORDER_ITEMS / STATUS_HISTORY / PAYMENTS / SHIPPING / INVOICES | `PayServiceImplementation` |
| POSTGRES_CATEGORIES / SELLERS / REVIEWS / ANALYTICS / ALERTS | `MainServiceImplementation`, `SellerServiceImplementation`, `AdminServiceImplementation` |

Suggested change for clothing: add `size` and `color` columns to `MYSQL_ORDER_ITEMS`
(the code already stores the chosen variant).

## Security measures

- CSRF token on every form; strict Content-Security-Policy (no inline JS/CSS), `X-Frame-Options`, `nosniff`.
- Passwords hashed with scrypt; 5 failed sign-ins lock the email for 15 minutes; same message for unknown email and wrong password.
- Session renewed on sign-in; user reloaded on each request; `?next=` only accepts local paths.
- Roles checked on the server (`login_required`, `seller_required`, `admin_required`).
- Ownership checks: buyers only see their orders and invoices; sellers only edit their products (`seller_id` always comes from the session).
- Prices and totals recalculated on the server; checkout validates stock and writes all tables at once.
- Card numbers are validated (Luhn, expiry, CVV) and discarded; only the last 4 digits are kept.
- DTOs whitelist fields and limit lengths; Jinja autoescaping on every template.
