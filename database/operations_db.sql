-- PostgreSQL: operational data of the marketplace
-- (categories, sellers, reviews, analytics, alerts).
--
-- 1. Create the database once (psql or pgAdmin, connected to "postgres"):
--        CREATE DATABASE operations_db;
--    Its name must be the same as PG_DATABASE in your .env.
-- 2. Connect to operations_db and run this whole file.
-- 3. Load the demo data:  python scripts/load_to_sql.py
--
-- WARNING: the DROP lines delete these 5 tables and their data.

DROP TABLE IF EXISTS alerts CASCADE;
DROP TABLE IF EXISTS analytics CASCADE;
DROP TABLE IF EXISTS reviews CASCADE;
DROP TABLE IF EXISTS sellers CASCADE;
DROP TABLE IF EXISTS categories CASCADE;

-- 1. Table: categories (tree: Women > Dresses ...)
CREATE TABLE categories (
    category_id SERIAL PRIMARY KEY,
    parent_id   INT NULL REFERENCES categories(category_id),
    name        VARCHAR(60) NOT NULL,
    slug        VARCHAR(60) NOT NULL UNIQUE,
    is_active   BOOLEAN NOT NULL DEFAULT TRUE
);

-- 2. Table: sellers (store of a seller user; user_id references DynamoDB USER#)
CREATE TABLE sellers (
    seller_id  VARCHAR(50) PRIMARY KEY,                -- SEL-0001
    user_id    VARCHAR(50) NOT NULL UNIQUE,
    store_name VARCHAR(60) NOT NULL,
    tax_id     VARCHAR(13) NOT NULL,                   -- RFC (Mexico)
    rating     NUMERIC(2, 1) NOT NULL DEFAULT 0,
    status     VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',  -- ACTIVE, SUSPENDED
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_sellers_rating CHECK (rating BETWEEN 0 AND 5),
    CONSTRAINT chk_sellers_tax_id CHECK (tax_id ~ '^[A-ZÑ&]{3,4}[0-9]{6}[A-Z0-9]{3}$')
);

-- 3. Table: reviews (one review per buyer and product)
CREATE TABLE reviews (
    review_id  SERIAL PRIMARY KEY,
    product_id VARCHAR(50) NOT NULL,                   -- References DynamoDB PRODUCT#
    buyer_id   VARCHAR(50) NOT NULL,                   -- References DynamoDB USER#
    rating     SMALLINT NOT NULL,
    comment    VARCHAR(500) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_reviews_product_buyer UNIQUE (product_id, buyer_id),
    CONSTRAINT chk_reviews_rating CHECK (rating BETWEEN 1 AND 5)
);
CREATE INDEX idx_reviews_product ON reviews(product_id);

-- 4. Table: analytics (units sold per product and day)
CREATE TABLE analytics (
    sale_id       SERIAL PRIMARY KEY,
    product_id    VARCHAR(50) NOT NULL,
    category_id   INT NOT NULL REFERENCES categories(category_id),
    seller_id     VARCHAR(50) NOT NULL REFERENCES sellers(seller_id),
    quantity_sold INT NOT NULL,
    sale_date     DATE NOT NULL DEFAULT CURRENT_DATE,
    CONSTRAINT chk_analytics_quantity CHECK (quantity_sold > 0)
);
CREATE INDEX idx_analytics_seller_date ON analytics(seller_id, sale_date);

-- 5. Table: alerts (low stock)
CREATE TABLE alerts (
    alert_id        SERIAL PRIMARY KEY,
    product_id      VARCHAR(50) NOT NULL,
    seller_id       VARCHAR(50) NOT NULL REFERENCES sellers(seller_id),
    current_stock   INT NOT NULL,
    threshold_limit INT NOT NULL,
    alert_status    VARCHAR(20) NOT NULL DEFAULT 'OPEN',   -- OPEN, RESOLVED
    updated_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT chk_alerts_values CHECK (current_stock >= 0 AND threshold_limit >= 0)
);
CREATE INDEX idx_alerts_seller ON alerts(seller_id, alert_status);
