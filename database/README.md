# Database scripts

| File | Engine | Tables |
|---|---|---|
| `finance_db.sql` | MySQL | orders, order_items, payments, shipping_addresses, invoices, order_status_history |
| `operations_db.sql` | PostgreSQL | categories, sellers, reviews, analytics, alerts |

DynamoDB has a single table, `MarketplaceDB` (PK, SK, GSI1PK, GSI1SK); it is
created in the AWS console, not with a script.

Both SQL files start by deleting their tables. If `python scripts/check_databases.py`
already shows ✓ for every table, you do not need to run them again.
