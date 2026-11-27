# Straw Denim · clothing marketplace (CRUD_Marketplace)

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
tests/           automated tests (pytest), including a fake DynamoDB table
scripts/         build_single_file_lambda.py (generates lambda_deploy/lambda_function.py),
                 load_to_dynamo.py (demo users + 17,000 products, BatchWriteItem), load_to_sql.py
                 (PostgreSQL demo data), export_seed_sql.py (writes that data as SQL), check_databases.py
                 (connections, tables, procedures, API routes), install_procedures.py,
                 verify_procedures_live.py (tests the procedures on a temporary database),
                 aws_deploy.py (deploys/checks AWS with the Learner Lab credentials)
lambda_deploy/   lambda_function.py: the whole Lambda in one file, generated from the layers
database/        DDL, stored procedures, initial data (DML) and the DynamoDB schema
docs/            diagrams, OpenAPI and the step-by-step guides (Spanish)
```

## Project deliverables (checklist of the course rubric)

| Deliverable | File |
|---|---|
| SQL tables (DDL) | [database/finance_db.sql](database/finance_db.sql) (MySQL, 6 tables + indexes), [database/operations_db.sql](database/operations_db.sql) (PostgreSQL, 5 tables) |
| Initial data (DML) | [database/operations_seed.sql](database/operations_seed.sql) (29 categories, 12 sellers, 27,068 reviews, sales, alerts), [database/finance_seed.sql](database/finance_seed.sql) (example orders written through the procedures) |
| Stored procedures (separate script) | [database/finance_procedures.sql](database/finance_procedures.sql) (4 MySQL procedures), [database/operations_procedures.sql](database/operations_procedures.sql) (2 PostgreSQL functions) — explained in [docs/STORED_PROCEDURES.md](docs/STORED_PROCEDURES.md) |
| DynamoDB schema and indexing | [database/dynamodb_schema.json](database/dynamodb_schema.json) (create-table input), [database/dynamodb_entities.json](database/dynamodb_entities.json) (entities, keys, access patterns) |
| Entity-Relationship diagram | [docs/DIAGRAMA_ER.md](docs/DIAGRAMA_ER.md) (16 entities in 3 databases, Mermaid) |
| AWS architecture diagram | [docs/ARQUITECTURA_AWS.md](docs/ARQUITECTURA_AWS.md) (C4 containers, security, purchase and cancel flows) |
| API specification | [docs/openapi.yaml](docs/openapi.yaml) (OpenAPI 3.0, 19 routes; import it in Postman or Swagger Editor) |
| Deployment manual | [docs/DESPLIEGUE_AWS.md](docs/DESPLIEGUE_AWS.md), [docs/BASES_DE_DATOS.md](docs/BASES_DE_DATOS.md), [docs/DYNAMODB_LAMBDA.md](docs/DYNAMODB_LAMBDA.md), [docs/FOTOS_S3.md](docs/FOTOS_S3.md) |
| Source code | backend and frontend below; automated tests in `tests/` |

**Where the backend and the frontend are** (the professor's layered architecture replaces `/src`):

| Part | Folders |
|---|---|
| Backend (Flask, Python) | `View/MainViews/PY/` (routes, security) → `Services/` → `Interfaces/` → `Implementation/` |
| Backend (AWS Lambda) | `Services/LambdaRouter.py` and `Services/*/…LambdaService.py`, generated into `lambda_deploy/lambda_function.py` |
| Frontend | `View/MainViews/HTML/` (Jinja templates), `View/MainViews/CSS/`, `View/MainViews/JS/`, `View/MainViews/IMG/` |

The Lambda reuses the same layers: `Services/LambdaRouter` → `Services/ProductService` and
`Services/UserService/UserLambdaService` (routes) → `Interfaces/ProductServiceInterface` and
`Interfaces/UserServiceInterface/UserItem` (validation) → `Implementation/ProductServiceImplementation`
and `Implementation/UserServiceImplementation/UserDynamoImplementation` (boto3 + DynamoDB).
Everything is stored in the single table MarketplaceDB. Setup: [docs/DYNAMODB_LAMBDA.md](docs/DYNAMODB_LAMBDA.md).

Data access goes through repositories (`Interfaces/DataInterfaces`): every one has a memory
version (demo, tests) and a real one — `MySQLFinanceRepository` (orders), `PostgresOperationsRepository`
(categories, sellers, reviews, analytics, alerts), `ApiQuickRepository` (carts, favorites) and
`ApiProductRepository` (products). Setup: [docs/BASES_DE_DATOS.md](docs/BASES_DE_DATOS.md).

## Run it

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/Mac: source .venv/bin/activate)
pip install -r requirements.txt
python View/MainViews/PY/app.py
```

Open http://127.0.0.1:5000

Quick demo without AWS (data in memory, nothing is saved): `MARKETPLACE_ENV_FILE=none python View/MainViews/PY/app.py`
(Git Bash; in cmd first `set MARKETPLACE_ENV_FILE=none`).

Demo accounts (password `Demo1234`):

| Email | Role |
| --- | --- |
| ana@example.com | buyer (has orders in every status) |
| carlos@urbanthread.mx | seller · Urban Thread (dashboard, alerts) |
| admin@marketplace.mx | admin (users, categories, order status) |

## Deploy to AWS (Learner Lab)

`python scripts/aws_deploy.py` checks and `--apply` deploys the Lambda, its API Gateway routes, the S3 photo bucket and the Lambda settings with the temporary Learner Lab credentials; `python scripts/install_procedures.py` installs the stored procedures. Step by step in Spanish: `docs/DESPLIEGUE_AWS.md`.

## Automated tests

```bash
python -m pytest -q
```

## Configuration (environment variables or a `.env` file — copy `.env.example`)

| Variable | Default | Meaning |
| --- | --- | --- |
| `FINANCE_BACKEND` | `memory` | `mysql`: orders, payments, shipping, invoices and history in MySQL `finance_db` (`MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD`, `MYSQL_DATABASE`). |
| `OPERATIONS_BACKEND` | `memory` | `postgres`: categories, sellers, reviews, analytics and alerts in PostgreSQL (`PG_HOST`, `PG_PORT`, `PG_USER`, `PG_PASSWORD`, `PG_DATABASE`). |
| `DATA_BACKEND` | `memory` | `aws`: users, carts and favorites live in DynamoDB MarketplaceDB through the Lambda. |
| `PRODUCTS_BACKEND` | `memory` | `aws`: the catalog is read from and written to DynamoDB MarketplaceDB through the Lambda. |
| `API_URL` | see api_client.py | API Gateway invoke URL (users). |
| `PRODUCTS_API_URL` | `API_URL` | API Gateway invoke URL for `/productos/...`. |
| `MARKETPLACE_API_KEY` | — | Same value as the Lambda's `MARKETPLACE_API_KEY` (users routes and product writes). Never commit it. |
| `PRODUCT_IMAGES_URL` | — | `https://<bucket>.s3.<region>.amazonaws.com`: the only external origin allowed for images (CSP). See `docs/FOTOS_S3.md`. |
| `PRODUCT_COUNT` | `17000` | Size of the generated catalog. |
| `SECRET_KEY` | random | Set it so sessions survive restarts. |
| `SESSION_COOKIE_SECURE` | off | Set `1` when served over HTTPS. |
| `FLASK_DEBUG` | off | `1` for auto-reload while developing. Never in production. |

## Where each table of the diagram lives

| Diagram | Code |
| --- | --- |
| DynamoDB MarketplaceDB (users, products, carts, favorites) | `USER#`, `PRODUCT#`, `CART#`, `FAV#` items through the Lambda (`DATA_BACKEND=aws`, `PRODUCTS_BACKEND=aws`) |
| MySQL finance_db: orders, order_items, payments, shipping_addresses, invoices, order_status_history | `MySQLFinanceRepository` (`FINANCE_BACKEND=mysql`) |
| PostgreSQL: categories, sellers, reviews, analytics, alerts | `PostgresOperationsRepository` (`OPERATIONS_BACKEND=postgres`) |

## Security measures

- CSRF token on every form; strict Content-Security-Policy (no inline JS/CSS), `X-Frame-Options`, `nosniff`.
- Passwords hashed with scrypt; 5 failed sign-ins lock the email for 15 minutes; same message for unknown email and wrong password.
- Session renewed on sign-in; user reloaded on each request; `?next=` only accepts local paths.
- Roles checked on the server (`login_required`, `seller_required`, `admin_required`).
- Ownership checks: buyers only see their orders and invoices; sellers only edit their products (`seller_id` always comes from the session).
- Prices and totals recalculated on the server; checkout validates stock and writes all tables at once.
- Atomic logic (see `docs/STORED_PROCEDURES.md`): stock is discounted/returned with DynamoDB `TransactWriteItems` (all products or none, a cancelled order restocks only once) and orders go through MySQL stored procedures (`sp_create_order`, `sp_change_order_status`, `sp_cancel_order` with row locks; `sp_buyer_sales_history`) and PostgreSQL functions (`fn_seller_sales_history`).
- Card numbers are validated (Luhn, expiry, CVV) and discarded; only the last 4 digits are kept.
- DTOs whitelist fields and limit lengths; Jinja autoescaping on every template.
- SQL: parameterized queries only (no SQL injection), one transaction per order / status change, optimistic check on status changes, friendly 503 page when a database is down, credentials only in `.env` (git-ignored).
- Lambda: users, carts, favorites and product writes require `x-api-key` (constant-time compare, refused if not configured); users are whitelisted and only accept password hashes; public product reads only see active products; every payload re-validated in the Lambda; stock changes are conditional updates (never below zero) and checkout undoes partial stock changes; errors never expose internals; IAM policy limited to one table and index.
