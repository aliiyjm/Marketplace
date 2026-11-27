-- finance_db (MySQL): transactional tables of the marketplace.
-- WARNING: the first line deletes the whole database and its data.
DROP DATABASE IF EXISTS finance_db;
CREATE DATABASE finance_db;
USE finance_db;

-- 1. Table: orders (Header for all financial transactions)
CREATE TABLE orders (
    order_id VARCHAR(50) PRIMARY KEY, -- References DynamoDB Order ID
    buyer_id VARCHAR(50) NOT NULL,    -- References DynamoDB User ID
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(20) NOT NULL,      -- PENDING, PAID, SHIPPED, DELIVERED, CANCELLED
    subtotal DECIMAL(10, 2) NOT NULL,
    tax_amount DECIMAL(10, 2) NOT NULL,
    total_amount DECIMAL(10, 2) NOT NULL
);

-- 2. Table: order_items (Items inside the order)
CREATE TABLE order_items (
    item_id INT AUTO_INCREMENT PRIMARY KEY,
    order_id VARCHAR(50) NOT NULL,
    product_id VARCHAR(50) NOT NULL,  -- References DynamoDB Product ID
    quantity INT NOT NULL,
    unit_price DECIMAL(10, 2) NOT NULL,
    size VARCHAR(20) NULL,            -- Chosen size (clothing variant)
    color VARCHAR(30) NULL,           -- Chosen color (clothing variant)
    CONSTRAINT fk_order_items_order FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE
);

-- 3. Table: payments (Payment tracking and methods)
CREATE TABLE payments (
    payment_id VARCHAR(50) PRIMARY KEY,
    order_id VARCHAR(50) NOT NULL,
    payment_method VARCHAR(30) NOT NULL, -- CARD, TRANSFER, CASH
    card_last4 CHAR(4) NULL,             -- Only the last 4 digits, never the full number
    status VARCHAR(20) NOT NULL,         -- SUCCESS, FAILED, PENDING
    processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_payments_order FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE
);

-- 4. Table: shipping_addresses (Delivery information)
CREATE TABLE shipping_addresses (
    address_id INT AUTO_INCREMENT PRIMARY KEY,
    order_id VARCHAR(50) NOT NULL,
    street VARCHAR(150) NOT NULL,
    city VARCHAR(100) NOT NULL,
    state VARCHAR(100) NOT NULL,
    zip_code VARCHAR(20) NOT NULL,
    CONSTRAINT fk_shipping_order FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE
);

-- 5. Table: invoices (PDF Receipts - Requirement 4)
CREATE TABLE invoices (
    invoice_id VARCHAR(50) PRIMARY KEY,
    order_id VARCHAR(50) NOT NULL UNIQUE,
    pdf_receipt_url VARCHAR(255) NOT NULL,
    issued_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_invoices_order FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE
);

-- 6. Table: order_status_history (Audit log for status changes)
CREATE TABLE order_status_history (
    history_id INT AUTO_INCREMENT PRIMARY KEY,
    order_id VARCHAR(50) NOT NULL,
    old_status VARCHAR(20),
    new_status VARCHAR(20) NOT NULL,
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_status_history_order FOREIGN KEY (order_id) REFERENCES orders(order_id) ON DELETE CASCADE
);

-- 7. Indexes for the queries the app runs most (the FOREIGN KEYs above already
--    index order_id in every child table). Existing databases get them with
--    python scripts/install_procedures.py (it only adds what is missing).
CREATE INDEX idx_orders_buyer_created  ON orders (buyer_id, created_at);  -- My orders, sp_buyer_sales_history
CREATE INDEX idx_orders_status_created ON orders (status, created_at);    -- admin orders filtered by status
CREATE INDEX idx_order_items_product   ON order_items (product_id);       -- "bought it?" check before a review
