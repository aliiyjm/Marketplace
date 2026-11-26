-- =====================================================================
-- PostgreSQL (operations data): STORED FUNCTIONS of the marketplace
-- =====================================================================
-- The tables are created by operations_db.sql. This file only (re)creates
-- the functions (CREATE OR REPLACE): it never deletes or changes data.
--
-- How to install (choose one):
--   a) python scripts/install_procedures.py      (uses your .env)
--   b) pgAdmin / DBeaver: open this file on your database and run it all.
--
-- PostgreSQL functions that RETURN TABLE are the way to write a stored
-- routine that gives back rows (a PROCEDURE cannot return a result set).
-- They are STABLE (read only) and every value is a parameter: no dynamic
-- SQL, so no SQL injection.
--
--   1. fn_seller_sales_history   consolidated history of a seller per product
--   2. fn_seller_units_by_day    units sold per day (dashboard chart)
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Consolidated sales history of a seller since a date:
--    units, days with sales, first and last sale of each product,
--    best sellers first.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_seller_sales_history(
    p_seller_id VARCHAR,
    p_since     DATE,
    p_limit     INTEGER DEFAULT 5
)
RETURNS TABLE (
    product_id      VARCHAR,
    units           BIGINT,
    days_with_sales BIGINT,
    first_sale      DATE,
    last_sale       DATE
)
LANGUAGE sql STABLE
AS $$
    SELECT a.product_id,
           SUM(a.quantity_sold)::BIGINT,
           COUNT(DISTINCT a.sale_date)::BIGINT,
           MIN(a.sale_date),
           MAX(a.sale_date)
      FROM analytics a
     WHERE a.seller_id = p_seller_id
       AND a.sale_date >= p_since
     GROUP BY a.product_id
     ORDER BY 2 DESC, 1
     LIMIT GREATEST(LEAST(p_limit, 100), 1);
$$;

-- ---------------------------------------------------------------------
-- 2. Units sold per day by a seller since a date.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION fn_seller_units_by_day(
    p_seller_id VARCHAR,
    p_since     DATE
)
RETURNS TABLE (
    sale_date DATE,
    units     BIGINT
)
LANGUAGE sql STABLE
AS $$
    SELECT a.sale_date, SUM(a.quantity_sold)::BIGINT
      FROM analytics a
     WHERE a.seller_id = p_seller_id
       AND a.sale_date >= p_since
     GROUP BY a.sale_date
     ORDER BY a.sale_date;
$$;
