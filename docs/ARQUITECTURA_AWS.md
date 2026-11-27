# Arquitectura en AWS (Learner Lab)

Región **us-east-1**. Todo corre con el rol preconfigurado **LabRole** (sin roles ni políticas IAM
propias) y con recursos del tamaño que permite el laboratorio.

## 1. Vista de contenedores (C4 nivel 2)

```mermaid
flowchart LR
    user(["Comprador / Vendedor / Admin<br/>navegador"])

    subgraph local["Servidor de la app (Flask 3, Python)"]
        flask["Straw Denim web<br/>View -> Services -> Interfaces -> Implementation<br/>CSRF, CSP, roles, sesiones"]
    end

    subgraph aws["AWS Learner Lab (us-east-1)"]
        apigw["API Gateway HTTP API<br/>19 rutas<br/>/productos /usuarios /carrito /favoritos"]
        lambda["Lambda CRUD_Marketplace<br/>Python, 256 MB, 30 s<br/>rol LabRole"]
        ddb[("DynamoDB MarketplaceDB<br/>tabla unica + GSI1<br/>On-Demand, 17,000 productos")]
        s3[("S3 marketplace-fotos-*<br/>fotos de producto<br/>lectura publica solo products/*")]
        mysql[("RDS MySQL 8.4 finance_db<br/>db.t4g.micro, 20 GB<br/>pedidos y pagos + 4 SP")]
        pg[("RDS PostgreSQL 18 postgres<br/>db.t4g.micro, 20 GB<br/>catalogo, resenas, analitica + 2 funciones")]
        cw["CloudWatch Logs"]
    end

    user -- "HTTPS / HTML" --> flask
    user -. "GET de fotos" .-> s3
    flask -- "HTTPS + JWT Bearer (60 s)<br/>(requests)" --> apigw
    apigw -- "AWS_PROXY" --> lambda
    lambda -- "GetItem, Query, Scan,<br/>TransactWriteItems" --> ddb
    lambda -- "PutObject / DeleteObject" --> s3
    lambda -- "errores (sin detalles al cliente)" --> cw
    flask -- "PyMySQL, SQL parametrizado,<br/>CALL sp_..." --> mysql
    flask -- "psycopg 3, SQL parametrizado,<br/>SELECT fn_..." --> pg
```

| Componente | Configuración | Por qué |
|---|---|---|
| **API Gateway** (HTTP API) | 19 rutas, **todas exigen un JWT**, todas a la misma integración de la Lambda, etapa con auto-deploy, **límite de 50 peticiones/s (ráfagas de 100)** | Un solo punto de entrada a DynamoDB/S3; la app no guarda credenciales de AWS |
| **Lambda** `CRUD_Marketplace` | Python, **256 MB**, timeout 30 s, rol **LabRole**, variables `MARKETPLACE_TABLE`, `MARKETPLACE_GSI`, `MARKETPLACE_API_KEY`, `MARKETPLACE_BUCKET` | Límite del lab: 128-256 MB; solo esta API puede invocarla |
| **DynamoDB** `MarketplaceDB` | On-Demand, PK/SK + GSI1 | Catálogo masivo y transacciones rápidas; sin capacidad aprovisionada que gaste crédito |
| **S3** | Bloqueo de ACLs, política de solo lectura de `products/*` | Las fotos se leen directo del bucket; subir y borrar solo lo hace la Lambda |
| **RDS MySQL** `db-finance` | `db.t4g.micro`, 20 GB, almacenamiento cifrado; Security Group: puerto 3306 solo para la IP del equipo de desarrollo | Datos financieros con transacciones ACID y stored procedures |
| **RDS PostgreSQL** `db-operations` | `db.t4g.micro`, 20 GB, almacenamiento cifrado; Security Group: puerto 5432 solo para la IP del equipo de desarrollo | Catálogos, reseñas y analítica con funciones de reporte |
| **CloudWatch** | Logs de la Lambda | Los errores internos se registran ahí; al cliente solo le llega `Internal error.` |

**Configuración sin direcciones fijas:** la app lee todos los endpoints de `.env` (`API_URL`, `MYSQL_HOST`,
`PG_HOST`, `PRODUCT_IMAGES_URL`), así que cuando el laboratorio cambia algo solo se edita ese archivo.
`scripts/aws_deploy.py` lee las **credenciales temporales** del Learner Lab (`AWS_ACCESS_KEY_ID`,
`AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`) para desplegar sin clics.

**Ahorro de crédito:** RDS de clase micro con 20 GB que se apagan al terminar de trabajar
(`--start-rds` para encenderlas); DynamoDB On-Demand; Lambda de 256 MB que solo cobra cuando se usa.

## 2. Seguridad en cada conexión

```mermaid
flowchart TB
    b["Navegador"] -->|"cookie de sesion HttpOnly + SameSite<br/>token CSRF en cada formulario<br/>CSP estricta, sin JS/CSS inline"| f["Flask"]
    f -->|"JWT HS256 firmado con la llave (la llave nunca viaja)<br/>caduca en 60 s, permisos read / write<br/>sin llave configurada la Lambda rechaza todo"| a["API Gateway -> Lambda"]
    a -->|"rol LabRole<br/>validacion de cada payload (DTOs)"| d[("DynamoDB / S3")]
    f -->|"usuario y contrasena en .env (no en Git)<br/>solo consultas con parametros %s"| r[("RDS MySQL / PostgreSQL")]
```

## 3. Flujo de compra (transacción atómica + stored procedure)

```mermaid
sequenceDiagram
    actor C as Comprador
    participant F as Flask
    participant L as Lambda
    participant D as DynamoDB
    participant M as MySQL finance_db
    participant P as PostgreSQL
    C->>F: POST /checkout
    F->>F: valida carrito, recalcula precios
    F->>L: POST /productos/reservar {order_id, items}
    L->>D: TransactWriteItems (reserva + stock -= qty de TODOS los productos)
    D-->>L: ok (o nada cambia si falta stock)
    L-->>F: stock nuevo
    F->>M: CALL sp_create_order(...) (pedido, detalle, pago, envio, historial)
    alt MySQL falla
        F->>L: POST /productos/liberar (devuelve el stock)
    end
    F->>M: CALL sp_change_order_status(PENDING -> PAID) si es tarjeta
    F->>P: INSERT analytics (ventas del dia)
    F-->>C: confirmacion del pedido
```

## 4. Flujo de cancelación

```mermaid
sequenceDiagram
    actor A as Admin
    participant F as Flask
    participant M as MySQL finance_db
    participant L as Lambda
    participant D as DynamoDB
    A->>F: POST /admin/orders/<id>/status (Cancelled)
    F->>M: CALL sp_cancel_order (SELECT ... FOR UPDATE, pago REFUNDED/CANCELLED, historial)
    M-->>F: unidades a devolver
    F->>L: POST /productos/liberar {order_id}
    L->>D: TransactWriteItems (reserva RESERVED -> RELEASED + stock += qty)
    Note over L,D: la condicion status = RESERVED impide reabastecer dos veces
    F-->>A: pedido cancelado
```

Más detalle: [DIAGRAMA_ER.md](DIAGRAMA_ER.md), [STORED_PROCEDURES.md](STORED_PROCEDURES.md),
[DESPLIEGUE_AWS.md](DESPLIEGUE_AWS.md), [openapi.yaml](openapi.yaml).
