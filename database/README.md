# Database scripts

| File | Engine | Tables |
|---|---|---|
| `finance_db.sql` | MySQL | orders, order_items, payments, shipping_addresses, invoices, order_status_history |
| `operations_db.sql` | PostgreSQL | categories, sellers, reviews, analytics, alerts |
| `finance_procedures.sql` | MySQL | stored procedures: `sp_create_order`, `sp_change_order_status`, `sp_cancel_order`, `sp_buyer_sales_history` |
| `operations_procedures.sql` | PostgreSQL | stored functions: `fn_seller_sales_history`, `fn_seller_units_by_day` |

The procedure files never touch data: install or update them with
`python scripts/install_procedures.py` (see `docs/STORED_PROCEDURES.md`).

DynamoDB has a single table, `MarketplaceDB` (PK, SK, GSI1PK, GSI1SK); it is
created in the AWS console, not with a script. Besides users, products, carts and
favorites it keeps one stock reservation per order (`RESERVATION#<order_id>` / `STOCK`),
written by the atomic transactions of the Lambda.

Both SQL files start by deleting their tables. If `python scripts/check_databases.py`
already shows ✓ for every table, you do not need to run them again.
