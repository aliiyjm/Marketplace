# Conectar la app a las bases de datos

Con esto la app deja de usar datos de ejemplo en memoria: todo se lee y se guarda en sus bases.

| Base | Qué guarda | Cómo la usa la app |
| --- | --- | --- |
| **MySQL `finance_db`** (tu compu) | pedidos, artículos, pagos, envíos, facturas, historial de estatus | lee y escribe en cada petición; cada pedido se guarda en **una sola transacción** |
| **PostgreSQL** (tu compu) | reseñas, ventas (analytics), alertas de stock | lee y escribe en cada petición |
| **PostgreSQL** | categorías y vendedores | los lee al arrancar y **cada minuto**: la búsqueda filtra 17,000 productos con ellos. Todo cambio se escribe primero en la base |
| **DynamoDB `MarketplaceDB`** (AWS) | usuarios, productos, carrito, favoritos | a través de API Gateway + Lambda ([DYNAMODB_LAMBDA.md](DYNAMODB_LAMBDA.md)) |

Las tablas son las de los scripts de la carpeta [`database/`](../database/) (`finance_db.sql` para MySQL y
`operations_db.sql` para PostgreSQL).
En la base se guardan los estatus como dicen sus scripts (`PENDING`, `PAID`, `CARD`, `TRANSFER`,
`SUCCESS`, `ACTIVE`, `OPEN`…); la app los traduce al mostrarlos.

---

## 1. Instala los drivers

```bash
pip install -r requirements.txt     # agrega PyMySQL (MySQL) y psycopg (PostgreSQL)
```

## 2. Crea tu archivo `.env`

Copia `.env.example` como `.env` (en la raíz del proyecto) y llena tus datos:

```ini
FINANCE_BACKEND=mysql
OPERATIONS_BACKEND=postgres
DATA_BACKEND=aws
PRODUCTS_BACKEND=aws

MYSQL_HOST=localhost
MYSQL_USER=root
MYSQL_PASSWORD=tu-contraseña
MYSQL_DATABASE=finance_db

PG_HOST=localhost
PG_USER=postgres
PG_PASSWORD=tu-contraseña
PG_DATABASE=operations_db        # el nombre de tu base de Postgres

API_URL=https://xxxx.execute-api.us-east-1.amazonaws.com
MARKETPLACE_API_KEY=la-misma-clave-de-la-Lambda
SECRET_KEY=cualquier-texto-largo-al-azar
```

- `.env` **no se sube a GitHub** (está en `.gitignore`): tiene contraseñas. Cada integrante tiene
  el suyo con los datos de su compu.
- Si una contraseña tiene `#` o espacios, ponla entre comillas: `MYSQL_PASSWORD="mi#clave"`.
- Recomendado: en vez de `root` / `postgres`, crea un usuario solo para la app con permisos de
  `SELECT, INSERT, UPDATE, DELETE` sobre estas tablas.

## 3. Carga los datos iniciales

**PostgreSQL** (categorías, vendedores, 27,068 reseñas, historial de ventas y alertas):

```bash
python scripts/load_to_sql.py --dry-run    # solo cuenta lo que cargaría
python scripts/load_to_sql.py              # carga (si ya hay datos, se niega)
python scripts/load_to_sql.py --replace    # borra los datos de demo de esas 5 tablas y vuelve a cargar
```

**MySQL `finance_db`** empieza vacía: los pedidos los crea la app cuando alguien compra.

**DynamoDB** (usuarios y productos), si no lo has hecho: `python scripts/load_to_dynamo.py`.
Los tres conjuntos salen de la misma semilla, así que los ids coinciden (productos, vendedores y
usuarios). Si usas `--count` distinto de 17000, usa el mismo en los dos scripts.

## 4. Verifica la conexión

```bash
python scripts/check_databases.py
```

Revisa que cada base responda y que estén **todas las tablas y columnas** que la app necesita (solo
lee; no cambia nada). Debe terminar con `All good.`. Si falta una columna, te dice cuál (por
ejemplo `size` en `order_items` si no corriste el script corregido).

## 5. Arranca la app

```bash
python View/MainViews/PY/app.py
```

Al arrancar muestra qué base usa cada parte, por ejemplo
`(data=aws, products=aws, finance=mysql, operations=postgres)`.

## Qué pasa en cada pantalla

| Pantalla | Base |
| --- | --- |
| Catálogo, búsqueda, filtros | productos de DynamoDB + categorías/vendedores y calificaciones de PostgreSQL |
| Producto | stock y precio al momento (DynamoDB); reseñas y distribución de estrellas (PostgreSQL) |
| Carrito, favoritos | DynamoDB (`CART#`, `FAV#`) |
| Pagar | descuenta stock en DynamoDB (atómico) → escribe en MySQL `orders`, `order_items`, `shipping_addresses`, `payments`, `order_status_history` en una transacción → con tarjeta: `invoices` + ventas en PostgreSQL `analytics` |
| Mis pedidos, detalle, factura | MySQL |
| Admin → cambiar estatus | MySQL (`orders` + `order_status_history` + `payments`, en una transacción); cancelar devuelve el stock |
| Escribir reseña | solo si compraste (MySQL); se guarda en PostgreSQL `reviews` |
| Dashboard del vendedor | PostgreSQL `analytics` (sumas por día y top de productos) y `alerts` |
| Admin → categorías | PostgreSQL `categories` |
| Registro como vendedor | DynamoDB (usuario) + PostgreSQL `sellers` |

## Seguridad

- Todas las consultas usan parámetros (`%s`): los datos nunca se pegan en el SQL, así que no hay
  inyección SQL. Los únicos nombres que se arman en el SQL (columnas de alertas, tablas de los
  scripts) salen de listas fijas del código.
- Un pedido se guarda completo o no se guarda (transacción). Si MySQL falla después de descontar el
  stock, el stock se devuelve.
- Cambiar un estatus verifica que el pedido siga en el estatus que se leyó (`WHERE status = …`): si
  dos administradores lo cambian a la vez, el segundo recibe un aviso en lugar de pisar al primero.
- Si una base se cae, la app muestra una página "We'll be right back" (503) en vez de un error; el
  detalle técnico solo va al log del servidor. El catálogo y la página de producto siguen funcionando.
- Las contraseñas solo viven en `.env`, que no se sube a GitHub.

## Problemas comunes

| Mensaje | Causa |
| --- | --- |
| `The PyMySQL driver is not installed` | falta `pip install -r requirements.txt` |
| `Could not connect to the database (MySQL finance_db)` | MySQL apagado, o usuario/contraseña/puerto incorrectos en `.env` |
| `PostgreSQL has no categories yet` | falta el paso 3 (`load_to_sql.py`) |
| `missing columns size, color` | `finance_db` se creó con el script viejo: corre `database/finance_db.sql` |
| `Access denied` / `password authentication failed` | usuario o contraseña incorrectos en `.env` |
| `Unknown database` / `database "…" does not exist` | `MYSQL_DATABASE` / `PG_DATABASE` no coincide con el nombre real de tu base |
| `Connection refused` | el servicio de MySQL o PostgreSQL no está iniciado (Windows: *Servicios*) |
| `Could not read the products from DynamoDB` | `API_URL` o `MARKETPLACE_API_KEY` incorrectos, o falta pegar la Lambda nueva |
| `found .env.txt but not .env` | el Bloc de notas agregó `.txt`: renombra el archivo a `.env` |
| `UnicodeEncodeError` al imprimir ✓ | versión vieja del script; ya está corregido |
| `PostgreSQL already has categories` | ya cargaste los datos; usa `--replace` si quieres recargarlos |
| Pedidos de Ana vacíos | es normal: con bases reales los pedidos empiezan en cero |
