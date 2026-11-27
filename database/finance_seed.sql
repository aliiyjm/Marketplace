-- =====================================================================
-- finance_db (MySQL 8.0+): INITIAL / EXAMPLE DATA (DML)
-- =====================================================================
-- The real orders are created by the app (checkout). These three example
-- orders show the data model AND the stored procedures working: every row is
-- written through the procedures of finance_procedures.sql, never with a
-- direct INSERT, exactly as the app does.
--
-- Requirements: tables (finance_db.sql) and procedures (finance_procedures.sql)
-- already created. Run it ONCE (order numbers 90001-90003 are unique) from
-- MySQL Workbench / DBeaver with the finance_db schema selected.
-- These examples do not touch DynamoDB: the stock of the products is not
-- discounted (the app does that with its atomic TransactWriteItems).
--
-- Buyer u-ana00001 (ana@example.com) and the products PRD-0000xx exist in
-- DynamoDB with these prices (MXN, IVA included).
-- =====================================================================

-- 1) Card order: created PENDING, then PAID (payment approved + invoice) and SHIPPED.
CALL sp_create_order('90001', 'u-ana00001', '2026-11-20 10:15:00',
     '[{"product_id": "PRD-000001", "quantity": 1, "unit_price": "1099.00", "size": "S", "color": "Red"},
       {"product_id": "PRD-000005", "quantity": 2, "unit_price": "299.00", "size": "M", "color": "Gray"}]',
     'Av. Universidad 100', 'Emiliano Zapata', 'Morelos', '62765', 'CARD', '4242');
CALL sp_change_order_status('90001', 'PENDING', 'PAID', '2026-11-20 10:15:05', '100001', '/orders/90001/invoice');
CALL sp_change_order_status('90001', 'PAID', 'SHIPPED', '2026-11-21 09:00:00', NULL, NULL);

-- 2) SPEI (bank transfer) order: still waiting for the payment.
CALL sp_create_order('90002', 'u-ana00001', '2026-11-22 18:40:00',
     '[{"product_id": "PRD-000003", "quantity": 1, "unit_price": "1099.00", "size": "26", "color": "White"}]',
     'Av. Universidad 100', 'Emiliano Zapata', 'Morelos', '62765', 'TRANSFER', NULL);

-- 3) Cash order cancelled by an admin (payment cancelled, history kept).
CALL sp_create_order('90003', 'u-ana00001', '2026-11-23 12:05:00',
     '[{"product_id": "PRD-000008", "quantity": 1, "unit_price": "1949.00", "size": "L", "color": "Black"}]',
     'Av. Universidad 100', 'Emiliano Zapata', 'Morelos', '62765', 'CASH', NULL);
CALL sp_cancel_order('90003', 'PENDING', '2026-11-23 15:30:00');

-- Consolidated history of the buyer (on an empty database: 3 orders, 2 not cancelled, $2,796.00 spent):
CALL sp_buyer_sales_history('u-ana00001');
