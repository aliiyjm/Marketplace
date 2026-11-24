"""SQLite copies of the two real scripts (finance_db.sql for MySQL and the
PostgreSQL operations script), used to test the SQL repositories without a
server. Same tables, columns, keys and constraints; only the syntax that
SQLite does not have changes (AUTO_INCREMENT / SERIAL -> AUTOINCREMENT, no
regex CHECK)."""
import sqlite3
from datetime import date, datetime
from decimal import Decimal

from Implementation.DataSource.SqlConnection import SqlDatabase

sqlite3.register_adapter(datetime, lambda d: d.isoformat(" "))
sqlite3.register_adapter(date, lambda d: d.isoformat())
sqlite3.register_adapter(Decimal, str)

FINANCE = """
CREATE TABLE orders (
    order_id VARCHAR(50) PRIMARY KEY, buyer_id VARCHAR(50) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, status VARCHAR(20) NOT NULL,
    subtotal DECIMAL(10, 2) NOT NULL, tax_amount DECIMAL(10, 2) NOT NULL, total_amount DECIMAL(10, 2) NOT NULL);
CREATE TABLE order_items (
    item_id INTEGER PRIMARY KEY AUTOINCREMENT, order_id VARCHAR(50) NOT NULL, product_id VARCHAR(50) NOT NULL,
    quantity INT NOT NULL, unit_price DECIMAL(10, 2) NOT NULL, size VARCHAR(20) NULL, color VARCHAR(30) NULL,
    FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE);
CREATE TABLE payments (
    payment_id VARCHAR(50) PRIMARY KEY, order_id VARCHAR(50) NOT NULL, payment_method VARCHAR(30) NOT NULL,
    card_last4 CHAR(4) NULL, status VARCHAR(20) NOT NULL, processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE);
CREATE TABLE shipping_addresses (
    address_id INTEGER PRIMARY KEY AUTOINCREMENT, order_id VARCHAR(50) NOT NULL, street VARCHAR(150) NOT NULL,
    city VARCHAR(100) NOT NULL, state VARCHAR(100) NOT NULL, zip_code VARCHAR(20) NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE);
CREATE TABLE invoices (
    invoice_id VARCHAR(50) PRIMARY KEY, order_id VARCHAR(50) NOT NULL UNIQUE,
    pdf_receipt_url VARCHAR(255) NOT NULL, issued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE);
CREATE TABLE order_status_history (
    history_id INTEGER PRIMARY KEY AUTOINCREMENT, order_id VARCHAR(50) NOT NULL, old_status VARCHAR(20),
    new_status VARCHAR(20) NOT NULL, changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE);
"""

OPERATIONS = """
CREATE TABLE categories (
    category_id INTEGER PRIMARY KEY AUTOINCREMENT, parent_id INT NULL, name VARCHAR(60) NOT NULL,
    slug VARCHAR(60) NOT NULL UNIQUE, is_active BOOLEAN NOT NULL DEFAULT TRUE,
    FOREIGN KEY (parent_id) REFERENCES categories(category_id));
CREATE TABLE sellers (
    seller_id VARCHAR(50) PRIMARY KEY, user_id VARCHAR(50) NOT NULL UNIQUE, store_name VARCHAR(60) NOT NULL,
    tax_id VARCHAR(13) NOT NULL, rating NUMERIC(2, 1) NOT NULL DEFAULT 0,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE', created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (rating BETWEEN 0 AND 5));
CREATE TABLE reviews (
    review_id INTEGER PRIMARY KEY AUTOINCREMENT, product_id VARCHAR(50) NOT NULL, buyer_id VARCHAR(50) NOT NULL,
    rating SMALLINT NOT NULL, comment VARCHAR(500) NOT NULL, created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (product_id, buyer_id), CHECK (rating BETWEEN 1 AND 5));
CREATE TABLE analytics (
    sale_id INTEGER PRIMARY KEY AUTOINCREMENT, product_id VARCHAR(50) NOT NULL, category_id INT NOT NULL,
    seller_id VARCHAR(50) NOT NULL, quantity_sold INT NOT NULL, sale_date DATE NOT NULL DEFAULT CURRENT_DATE,
    FOREIGN KEY (category_id) REFERENCES categories(category_id),
    FOREIGN KEY (seller_id) REFERENCES sellers(seller_id), CHECK (quantity_sold > 0));
CREATE TABLE alerts (
    alert_id INTEGER PRIMARY KEY AUTOINCREMENT, product_id VARCHAR(50) NOT NULL, seller_id VARCHAR(50) NOT NULL,
    current_stock INT NOT NULL, threshold_limit INT NOT NULL, alert_status VARCHAR(20) NOT NULL DEFAULT 'OPEN',
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (seller_id) REFERENCES sellers(seller_id), CHECK (current_stock >= 0 AND threshold_limit >= 0));
"""


def sqlite_database(path, ddl, label):
    def connect():
        conn = sqlite3.connect(path)
        conn.execute("PRAGMA foreign_keys = ON")     # like MySQL / PostgreSQL
        return conn
    conn = connect()
    conn.executescript(ddl)
    conn.close()
    return SqlDatabase(connect, "sqlite", label)


def raw_rows(db, sql):
    with db.transaction() as cur:
        return cur.execute(sql).fetchall()
