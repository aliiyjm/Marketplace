-- =====================================================================
-- finance_db (MySQL 8.0+): STORED PROCEDURES of the marketplace
-- =====================================================================
-- The tables are created by finance_db.sql. This file only (re)creates the
-- procedures: it never deletes or changes data, so it can be run again.
--
-- How to install (choose one):
--   a) python scripts/install_procedures.py          (uses your .env)
--   b) MySQL Workbench / DBeaver: select the finance_db schema, open this
--      file and run it all (the DELIMITER lines are for those tools).
--
-- Every procedure:
--   * runs inside ONE transaction: everything is saved or nothing is
--     (EXIT HANDLER -> ROLLBACK -> RESIGNAL the original error);
--   * locks the order row it changes (SELECT ... FOR UPDATE), so two admins
--     or two app instances cannot change the same order at the same time;
--   * reports business errors with SIGNAL SQLSTATE '45000' and a short code
--     (ORDER_NOT_FOUND, STALE_ORDER, INVALID_TRANSITION, ...) that the app
--     translates to a friendly message;
--   * receives every value as a parameter: no dynamic SQL, so no SQL injection.
--
--   1. sp_create_order          order + items + shipping + payment + history
--   2. sp_change_order_status   PENDING->PAID->SHIPPED->DELIVERED (+ payment, invoice)
--   3. sp_cancel_order          cancel + payment refund/cancel + history,
--                               returns the units to put back in stock
--   4. sp_buyer_sales_history   consolidated history of a buyer
--
-- The stock lives in DynamoDB: the app discounts it with an atomic
-- TransactWriteItems before calling sp_create_order, and gives it back with
-- another one after sp_cancel_order (see docs/STORED_PROCEDURES.md).
-- =====================================================================

DELIMITER $$

-- ---------------------------------------------------------------------
-- 1. Create an order (transactional)
--    p_items: JSON array [{"product_id","quantity","unit_price","size","color"}]
--    Prices include IVA (16 %): subtotal and tax are computed HERE from the
--    lines, never taken from the client.
-- ---------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_create_order $$
CREATE PROCEDURE sp_create_order(
    IN p_order_id   VARCHAR(50),
    IN p_buyer_id   VARCHAR(50),
    IN p_created_at DATETIME,
    IN p_items      JSON,
    IN p_street     VARCHAR(150),
    IN p_city       VARCHAR(100),
    IN p_state      VARCHAR(100),
    IN p_zip_code   VARCHAR(20),
    IN p_method     VARCHAR(30),
    IN p_card_last4 CHAR(4)
)
BEGIN
    DECLARE v_lines    INT DEFAULT 0;
    DECLARE v_bad      INT DEFAULT 0;
    DECLARE v_total    DECIMAL(12, 2) DEFAULT 0;
    DECLARE v_subtotal DECIMAL(12, 2) DEFAULT 0;
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    IF p_order_id IS NULL OR p_order_id NOT REGEXP '^[0-9]{1,12}$' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'INVALID_ORDER_ID';
    END IF;
    IF p_method NOT IN ('CARD', 'TRANSFER', 'CASH') THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'INVALID_PAYMENT_METHOD';
    END IF;
    -- Only the last 4 digits of a card are ever stored (never the number or the CVV).
    IF p_method = 'CARD' AND (p_card_last4 IS NULL OR p_card_last4 NOT REGEXP '^[0-9]{4}$') THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'INVALID_CARD_LAST4';
    END IF;
    IF p_zip_code NOT REGEXP '^[0-9]{5}$' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'INVALID_ZIP_CODE';
    END IF;
    IF p_items IS NULL OR JSON_TYPE(p_items) <> 'ARRAY' OR JSON_LENGTH(p_items) = 0 THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'EMPTY_ORDER';
    END IF;

    SELECT COUNT(*),
           SUM(CASE WHEN j.product_id REGEXP '^PRD-[0-9]{6}$'
                         AND j.quantity BETWEEN 1 AND 10
                         AND j.unit_price > 0 THEN 0 ELSE 1 END),
           SUM(j.quantity * j.unit_price)
      INTO v_lines, v_bad, v_total
      FROM JSON_TABLE(p_items, '$[*]' COLUMNS (
               product_id VARCHAR(50)    PATH '$.product_id',
               quantity   INT            PATH '$.quantity',
               unit_price DECIMAL(10, 2) PATH '$.unit_price')) AS j;

    IF v_lines > 30 OR v_bad > 0 OR v_total IS NULL THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'INVALID_ORDER_ITEMS';
    END IF;

    SET v_subtotal = ROUND(CAST(v_total AS DECIMAL(30, 10)) / 1.16, 2);

    START TRANSACTION;
        INSERT INTO orders (order_id, buyer_id, created_at, status, subtotal, tax_amount, total_amount)
        VALUES (p_order_id, p_buyer_id, p_created_at, 'PENDING', v_subtotal, v_total - v_subtotal, v_total);

        INSERT INTO order_items (order_id, product_id, quantity, unit_price, size, color)
        SELECT p_order_id, j.product_id, j.quantity, j.unit_price, j.size, j.color
          FROM JSON_TABLE(p_items, '$[*]' COLUMNS (
                   product_id VARCHAR(50)    PATH '$.product_id',
                   quantity   INT            PATH '$.quantity',
                   unit_price DECIMAL(10, 2) PATH '$.unit_price',
                   size       VARCHAR(20)    PATH '$.size',
                   color      VARCHAR(30)    PATH '$.color')) AS j;

        INSERT INTO shipping_addresses (order_id, street, city, state, zip_code)
        VALUES (p_order_id, p_street, p_city, p_state, p_zip_code);

        INSERT INTO payments (payment_id, order_id, payment_method, card_last4, status, processed_at)
        VALUES (CONCAT('PAY-', p_order_id), p_order_id, p_method,
                IF(p_method = 'CARD', p_card_last4, NULL), 'PENDING', p_created_at);

        INSERT INTO order_status_history (order_id, old_status, new_status, changed_at)
        VALUES (p_order_id, NULL, 'PENDING', p_created_at);
    COMMIT;

    SELECT p_order_id AS order_id, v_subtotal AS subtotal,
           v_total - v_subtotal AS tax_amount, v_total AS total_amount;
END $$

-- ---------------------------------------------------------------------
-- 2. Change the status of an order (not cancellations: see sp_cancel_order)
--    Allowed: PENDING->PAID, PAID->SHIPPED, SHIPPED->DELIVERED.
--    p_old_status = the status the app saw (optimistic concurrency check).
--    PAID also approves the payment and issues the invoice.
-- ---------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_change_order_status $$
CREATE PROCEDURE sp_change_order_status(
    IN p_order_id   VARCHAR(50),
    IN p_old_status VARCHAR(20),
    IN p_new_status VARCHAR(20),
    IN p_changed_at DATETIME,
    IN p_invoice_id VARCHAR(50),
    IN p_pdf_url    VARCHAR(255)
)
BEGIN
    DECLARE v_current VARCHAR(20) DEFAULT NULL;
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    IF p_new_status = 'CANCELLED' THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'USE_SP_CANCEL_ORDER';
    END IF;
    IF NOT ((p_old_status = 'PENDING' AND p_new_status = 'PAID')
         OR (p_old_status = 'PAID'    AND p_new_status = 'SHIPPED')
         OR (p_old_status = 'SHIPPED' AND p_new_status = 'DELIVERED')) THEN
        SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'INVALID_TRANSITION';
    END IF;

    START TRANSACTION;
        SELECT status INTO v_current FROM orders WHERE order_id = p_order_id FOR UPDATE;
        IF v_current IS NULL THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'ORDER_NOT_FOUND';
        END IF;
        IF v_current <> p_old_status THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'STALE_ORDER';
        END IF;

        UPDATE orders SET status = p_new_status WHERE order_id = p_order_id;

        INSERT INTO order_status_history (order_id, old_status, new_status, changed_at)
        VALUES (p_order_id, v_current, p_new_status, p_changed_at);

        IF p_new_status = 'PAID' THEN
            UPDATE payments SET status = 'SUCCESS', processed_at = p_changed_at WHERE order_id = p_order_id;
            INSERT INTO invoices (invoice_id, order_id, pdf_receipt_url, issued_at)
            VALUES (p_invoice_id, p_order_id, p_pdf_url, p_changed_at);
        END IF;
    COMMIT;
END $$

-- ---------------------------------------------------------------------
-- 3. Cancel an order and return what has to go back to stock
--    Only PENDING or PAID orders. A PAID order is refunded, a PENDING one
--    has its payment cancelled. The result set (product_id, quantity) is
--    what the app gives back to DynamoDB with an atomic transaction.
-- ---------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_cancel_order $$
CREATE PROCEDURE sp_cancel_order(
    IN p_order_id   VARCHAR(50),
    IN p_old_status VARCHAR(20),
    IN p_changed_at DATETIME
)
BEGIN
    DECLARE v_current VARCHAR(20) DEFAULT NULL;
    DECLARE EXIT HANDLER FOR SQLEXCEPTION
    BEGIN
        ROLLBACK;
        RESIGNAL;
    END;

    START TRANSACTION;
        SELECT status INTO v_current FROM orders WHERE order_id = p_order_id FOR UPDATE;
        IF v_current IS NULL THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'ORDER_NOT_FOUND';
        END IF;
        IF v_current <> p_old_status THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'STALE_ORDER';
        END IF;
        IF v_current NOT IN ('PENDING', 'PAID') THEN
            SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = 'CANNOT_CANCEL';
        END IF;

        UPDATE orders SET status = 'CANCELLED' WHERE order_id = p_order_id;

        UPDATE payments
           SET status = IF(v_current = 'PAID', 'REFUNDED', 'CANCELLED'), processed_at = p_changed_at
         WHERE order_id = p_order_id;

        INSERT INTO order_status_history (order_id, old_status, new_status, changed_at)
        VALUES (p_order_id, v_current, 'CANCELLED', p_changed_at);
    COMMIT;

    SELECT product_id, SUM(quantity) AS quantity
      FROM order_items
     WHERE order_id = p_order_id
     GROUP BY product_id
     ORDER BY product_id;
END $$

-- ---------------------------------------------------------------------
-- 4. Consolidated sales history of a buyer
--    One row per order (lines, units) plus the totals of the buyer in every
--    row (window functions): orders, money spent and units bought, without
--    counting cancelled orders.
-- ---------------------------------------------------------------------
DROP PROCEDURE IF EXISTS sp_buyer_sales_history $$
CREATE PROCEDURE sp_buyer_sales_history(
    IN p_buyer_id VARCHAR(50)
)
BEGIN
    SELECT h.order_id, h.created_at, h.status, h.total_amount, h.line_count, h.units,
           COUNT(*) OVER ()                                                AS orders_count,
           SUM(IF(h.status <> 'CANCELLED', 1, 0)) OVER ()                  AS active_orders,
           SUM(IF(h.status <> 'CANCELLED', h.total_amount, 0)) OVER ()     AS total_spent,
           SUM(IF(h.status <> 'CANCELLED', h.units, 0)) OVER ()            AS units_bought
      FROM (SELECT o.order_id, o.created_at, o.status, o.total_amount,
                   COUNT(i.item_id)             AS line_count,
                   COALESCE(SUM(i.quantity), 0) AS units
              FROM orders o
              LEFT JOIN order_items i ON i.order_id = o.order_id
             WHERE o.buyer_id = p_buyer_id
             GROUP BY o.order_id, o.created_at, o.status, o.total_amount) AS h
     ORDER BY h.created_at DESC, h.order_id DESC;
END $$

DELIMITER ;
