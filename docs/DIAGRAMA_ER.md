# Diagrama Entidad-Relación (DER)

Straw Denim usa un **diseño híbrido de 3 bases de datos**. Cada dato vive en la base que mejor le queda:

| Base | Motor | Entidades |
|---|---|---|
| `MarketplaceDB` | **DynamoDB** (tabla única, On-Demand) | Usuarios, Productos (con su inventario), Carritos, Favoritos, Reservas de stock |
| `finance_db` | **MySQL 8** (RDS) | Pedidos, Detalle de pedido, Pagos, Direcciones de envío, Facturas, Historial de estados |
| `postgres` | **PostgreSQL 18** (RDS) | Categorías, Vendedores, Reseñas, Analítica de ventas, Alertas de inventario |

**16 entidades** en total (la rúbrica pide al menos 10). Las líneas continuas son llaves foráneas reales
(la base las hace cumplir); las relaciones entre bases distintas son **lógicas**: las cuida la aplicación
(los ids `PRD-000001`, `SEL-0001`, `u-ana00001` son los mismos en las tres bases).

> Los diagramas están en Mermaid: GitHub los dibuja al abrir este archivo. Se pueden copiar en
> <https://mermaid.live> para exportarlos como imagen.

---

## 1. Vista general entre las tres bases

```mermaid
erDiagram
    USER ||--o{ ORDERS : "compra (buyer_id)"
    USER ||--o| SELLERS : "es vendedor (user_id)"
    USER ||--o| CART : "tiene"
    USER ||--o| FAVORITES : "guarda"
    USER ||--o{ REVIEWS : "escribe (buyer_id)"
    SELLERS ||--o{ PRODUCT : "vende (seller_id)"
    CATEGORIES ||--o{ PRODUCT : "clasifica (category_id)"
    PRODUCT ||--o{ ORDER_ITEMS : "se vende en (product_id)"
    PRODUCT ||--o{ REVIEWS : "recibe"
    PRODUCT ||--o{ ALERTS : "stock bajo"
    PRODUCT ||--o{ ANALYTICS : "ventas"
    ORDERS ||--|| STOCK_RESERVATION : "aparta stock (order_id)"
    ORDERS ||--|{ ORDER_ITEMS : "contiene"

    USER { string id_user "DynamoDB" }
    PRODUCT { string product_id "DynamoDB" }
    CART { string id_user "DynamoDB" }
    FAVORITES { string id_user "DynamoDB" }
    STOCK_RESERVATION { string order_id "DynamoDB" }
    ORDERS { varchar order_id "MySQL" }
    ORDER_ITEMS { int item_id "MySQL" }
    SELLERS { varchar seller_id "PostgreSQL" }
    CATEGORIES { int category_id "PostgreSQL" }
    REVIEWS { int review_id "PostgreSQL" }
    ANALYTICS { int sale_id "PostgreSQL" }
    ALERTS { int alert_id "PostgreSQL" }
```

---

## 2. MySQL `finance_db` (transaccional: pedidos y pagos)

Todas las tablas hijas apuntan a `orders` con `FOREIGN KEY ... ON DELETE CASCADE`.
Índices extra: `idx_orders_buyer_created (buyer_id, created_at)`, `idx_orders_status_created (status, created_at)`,
`idx_order_items_product (product_id)`. Escrituras solo por stored procedures (`sp_create_order`,
`sp_change_order_status`, `sp_cancel_order`).

```mermaid
erDiagram
    ORDERS ||--|{ ORDER_ITEMS : "contiene"
    ORDERS ||--|| PAYMENTS : "se paga con"
    ORDERS ||--|| SHIPPING_ADDRESSES : "se envía a"
    ORDERS ||--o| INVOICES : "factura (al pagar)"
    ORDERS ||--|{ ORDER_STATUS_HISTORY : "historial"

    ORDERS {
        varchar order_id PK "10001, 10002..."
        varchar buyer_id "USER en DynamoDB (indexado)"
        timestamp created_at
        varchar status "PENDING PAID SHIPPED DELIVERED CANCELLED"
        decimal subtotal "sin IVA"
        decimal tax_amount "IVA 16%"
        decimal total_amount
    }
    ORDER_ITEMS {
        int item_id PK
        varchar order_id FK
        varchar product_id "PRODUCT en DynamoDB (indexado)"
        int quantity "1 a 10"
        decimal unit_price "precio al comprar"
        varchar size
        varchar color
    }
    PAYMENTS {
        varchar payment_id PK "PAY-<order_id>"
        varchar order_id FK
        varchar payment_method "CARD TRANSFER CASH"
        char card_last4 "solo 4 digitos, nunca la tarjeta ni el CVV"
        varchar status "SUCCESS PENDING CANCELLED REFUNDED FAILED"
        timestamp processed_at
    }
    SHIPPING_ADDRESSES {
        int address_id PK
        varchar order_id FK
        varchar street
        varchar city
        varchar state
        varchar zip_code "5 digitos"
    }
    INVOICES {
        varchar invoice_id PK
        varchar order_id FK "UNIQUE"
        varchar pdf_receipt_url
        timestamp issued_at
    }
    ORDER_STATUS_HISTORY {
        int history_id PK
        varchar order_id FK
        varchar old_status
        varchar new_status
        timestamp changed_at
    }
```

---

## 3. PostgreSQL (operaciones: catálogo, vendedores y analítica)

Índices: `idx_reviews_product (product_id)`, `idx_analytics_seller_date (seller_id, sale_date)`,
`idx_alerts_seller (seller_id, alert_status)`; únicos: `categories.slug`, `sellers.user_id`,
`reviews (product_id, buyer_id)`. Funciones: `fn_seller_sales_history`, `fn_seller_units_by_day`.

```mermaid
erDiagram
    CATEGORIES ||--o{ CATEGORIES : "subcategoria (parent_id)"
    CATEGORIES ||--o{ ANALYTICS : "agrupa"
    SELLERS ||--o{ ANALYTICS : "vende"
    SELLERS ||--o{ ALERTS : "recibe"

    CATEGORIES {
        serial category_id PK
        int parent_id FK "NULL = categoria principal"
        varchar name
        varchar slug UK
        boolean is_active
    }
    SELLERS {
        varchar seller_id PK "SEL-0001"
        varchar user_id UK "USER en DynamoDB"
        varchar store_name
        varchar tax_id "RFC (CHECK con expresion regular)"
        numeric rating "0 a 5"
        varchar status "ACTIVE SUSPENDED"
        timestamp created_at
    }
    REVIEWS {
        serial review_id PK
        varchar product_id "PRODUCT en DynamoDB (indexado)"
        varchar buyer_id "USER en DynamoDB"
        smallint rating "1 a 5"
        varchar comment
        timestamp created_at
    }
    ANALYTICS {
        serial sale_id PK
        varchar product_id "PRODUCT en DynamoDB"
        int category_id FK
        varchar seller_id FK
        int quantity_sold "mayor que 0"
        date sale_date
    }
    ALERTS {
        serial alert_id PK
        varchar product_id "PRODUCT en DynamoDB"
        varchar seller_id FK
        int current_stock
        int threshold_limit
        varchar alert_status "OPEN RESOLVED"
        timestamp updated_at
    }
```

`reviews` tiene la restricción `UNIQUE (product_id, buyer_id)`: una reseña por comprador y producto.

---

## 4. DynamoDB `MarketplaceDB` (tabla única)

Una sola tabla con llave compuesta **PK + SK** y un índice global **GSI1 (GSI1PK, GSI1SK)**
(en el diagrama: `partition_key`, `sort_key`, `gsi1_partition`, `gsi1_sort`).
Esquema exacto: [`database/dynamodb_schema.json`](../database/dynamodb_schema.json); entidades y
patrones de acceso: [`database/dynamodb_entities.json`](../database/dynamodb_entities.json).

```mermaid
erDiagram
    USER ||--o| CART : "PK CART#id_user"
    USER ||--o| FAVORITES : "PK FAV#id_user"
    PRODUCT ||--o{ STOCK_RESERVATION : "items"

    USER {
        string partition_key PK "USER#id_user"
        string sort_key "PROFILE"
        string gsi1_partition "EMAIL#email (busqueda al iniciar sesion)"
        string gsi1_sort "USER#id_user"
        string name
        string role "buyer seller admin"
        string password_hash "Argon2id, nunca la contrasena"
    }
    PRODUCT {
        string partition_key PK "PRODUCT#PRD-000001 (17,000)"
        string sort_key "METADATA"
        string gsi1_partition "CATEGORY#slug"
        string gsi1_sort "PRICE#0000499.00 (orden por precio)"
        number price
        number stock "inventario, nunca menor que 0"
        string status "active draft deleted"
        map attributes "material, corte, cuidado..."
        string image_url "foto en S3 (opcional)"
    }
    CART {
        string partition_key PK "CART#id_user"
        string sort_key "CART"
        list lines "hasta 30 lineas"
    }
    FAVORITES {
        string partition_key PK "FAV#id_user"
        string sort_key "FAV"
        map items "hasta 200 productos"
    }
    STOCK_RESERVATION {
        string partition_key PK "RESERVATION#order_id"
        string sort_key "STOCK"
        string status "RESERVED RELEASED"
        map items "product_id y unidades"
    }
```
