# Usuarios y productos en DynamoDB (una sola tabla: MarketplaceDB)

```
Flask (View) ──HTTP──> API Gateway ──> Lambda (lambda_function.py) ──> DynamoDB MarketplaceDB
   api_client.py   (/usuarios/...)        LambdaRouter
   product_api.py  (/productos/...)         ├─ UserLambdaService  → UserDynamoImplementation
                                             └─ ProductService     → ProductServiceImplementation
```

Todo vive en **MarketplaceDB** (diseño de tabla única). Cada tipo de dato se distingue por sus llaves:

| Dato | PK | SK | GSI1PK | GSI1SK | Para qué sirve el índice |
| --- | --- | --- | --- | --- | --- |
| Usuario | `USER#<id_user>` | `PROFILE` | `EMAIL#<correo>` | `USER#<id_user>` | encontrar al usuario por correo al iniciar sesión |
| Producto | `PRODUCT#PRD-000123` | `METADATA` | `CATEGORY#<slug>` | `PRICE#0000499.00` | navegar una categoría ordenada por precio |
| Carrito | `CART#<id_user>` | `CART` | — | — | — |
| Favoritos | `FAV#<id_user>` | `FAV` | — | — | — |

El resto de los atributos (nombre, rol, hash de contraseña; precio, stock, tallas, colores y
`attributes` con el JSON flexible de cada prenda) va dentro del mismo ítem.

---

## 1. Revisa la tabla MarketplaceDB

En **DynamoDB → Tablas → MarketplaceDB**:

- Clave de partición `PK` (String) y clave de ordenación `SK` (String).
- Índice global **`GSI1`** con partición `GSI1PK` (String), ordenación `GSI1SK` (String) y
  proyección **All**. Si tu índice se llama distinto, pon ese nombre en `MARKETPLACE_GSI`.
- Capacidad **On-demand**. Con capacidad aprovisionada baja, la carga de 17,000 productos se frena.

## 2. Pega el código en tu Lambda

El código completo está en **un solo archivo**: `lambda_deploy/lambda_function.py`. Se genera a
partir de las capas del proyecto con `python scripts/build_single_file_lambda.py`; no lo edites a mano.

1. En la consola de tu Lambda abre `lambda_function.py`, **borra todo** su contenido y pega el
   del archivo `lambda_deploy/lambda_function.py`. Ya incluye usuarios, productos, carrito y favoritos.
2. **Runtime settings → Handler:** `lambda_function.lambda_handler` (es el que ya tenías).
3. Presiona **Deploy**.
4. **Configuración → Configuración general:** tiempo de espera **30 s** y memoria **512 MB**.
5. **Configuración → Variables de entorno:**

   | Variable | Valor |
   | --- | --- |
   | `MARKETPLACE_TABLE` | `MarketplaceDB` |
   | `MARKETPLACE_GSI` | `GSI1` |
   | `MARKETPLACE_API_KEY` | una clave larga y secreta: `python -c "import secrets; print(secrets.token_urlsafe(32))"` |

   La clave protege **todas** las rutas de usuarios y las que escriben productos. **No la subas
   a GitHub.** Si se filtra, cámbiala en la Lambda y en tu PC.

## 3. Permisos de la Lambda sobre la tabla

**Configuración → Permisos → nombre del rol → Agregar permisos → Crear política insertada →
JSON**. Cambia `REGION` y `CUENTA` por los tuyos, por ejemplo `us-east-1` y tu número de cuenta.
Si el rol tenía permisos sobre `T_Users`, ya puedes quitarlos.

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem",
               "dynamodb:Query", "dynamodb:Scan", "dynamodb:BatchWriteItem"],
    "Resource": ["arn:aws:dynamodb:REGION:CUENTA:table/MarketplaceDB",
                 "arn:aws:dynamodb:REGION:CUENTA:table/MarketplaceDB/index/GSI1"]
  }]
}
```

## 4. Rutas en API Gateway

Todas apuntan a la misma Lambda. Las de usuarios ya las tienes; agrega las de productos:

| Método | Ruta | Clave |
| --- | --- | --- |
| GET | `/usuarios/consultar` (`?id_user=` o `?email=`) | sí |
| POST | `/usuarios/agregar` | sí |
| PUT | `/usuarios/modificar` | sí |
| DELETE | `/usuarios/eliminar` (`?id_user=`) | sí |
| GET | `/productos/consultar` (`?product_id=` o `?limit=&cursor=&category=`) | no (sin clave solo ve productos activos) |
| POST | `/productos/agregar` | sí |
| PUT | `/productos/modificar` | sí |
| DELETE | `/productos/eliminar` (`?product_id=`) | sí |
| POST | `/productos/stock` | sí |
| POST | `/productos/cargar` | sí |
| GET | `/carrito/consultar` (`?id_user=`) | sí |
| PUT | `/carrito/guardar` | sí |
| DELETE | `/carrito/vaciar` (`?id_user=`) | sí |
| GET | `/favoritos/consultar` (`?id_user=`) | sí |
| PUT | `/favoritos/guardar` | sí |

Recomendado: en **Throttling** pon un límite (por ejemplo 50 solicitudes/s, ráfaga 100).

Prueba en Postman: `GET {tu-url}/productos/consultar?limit=5` → `{"items": [], "next_cursor": null}`.
Si haces `GET {tu-url}/usuarios/consultar` sin el encabezado `x-api-key`, debe responder **403**.

## 5. Sube los usuarios de prueba y las 17,000 prendas

En Git Bash, desde la raíz del proyecto:

```bash
export API_URL="https://xxxx.execute-api.us-east-1.amazonaws.com"
export MARKETPLACE_API_KEY="la-misma-MARKETPLACE_API_KEY-de-la-Lambda"
python scripts/load_to_dynamo.py --dry-run   # solo muestra qué enviaría
python scripts/load_to_dynamo.py             # sube usuarios + productos (pide confirmación)
```

- **Usuarios:** sube Ana, Carlos, el admin y los clientes y vendedores de prueba (contraseña
  `Demo1234`). Los que ya existen se saltan; nunca se sobrescriben.
- **Productos:** son 170 llamadas de 100 y tarda unos minutos. Si se corta, sigue con
  `--skip-users --start 5200` (el número exacto te lo da el script). Volver a subir productos
  **sobrescribe** su stock.

## 6. Corre la app leyendo de DynamoDB

```bash
export DATA_BACKEND=aws          # usuarios desde MarketplaceDB
export PRODUCTS_BACKEND=aws      # productos desde MarketplaceDB
export API_URL="https://xxxx.execute-api.us-east-1.amazonaws.com"
export MARKETPLACE_API_KEY="la-misma-MARKETPLACE_API_KEY-de-la-Lambda"
python View/MainViews/PY/app.py
```

- **Iniciar sesión** busca al usuario por correo con el índice `GSI1`, sin recorrer la tabla.
  **Registrarse** y el **CRUD de usuarios del admin** escriben en `MarketplaceDB`.
- **Comprar** resta el stock de forma atómica; si dos personas compran la última pieza, DynamoDB
  rechaza a la segunda y no se descuenta nada de su pedido. **Cancelar** devuelve el stock.
- **Crear, editar o borrar productos** desde el centro de vendedor escribe en DynamoDB.
- **La página de un producto** vuelve a leerlo de la tabla, así que siempre muestra precio y
  stock reales. Los listados salen de la copia que se carga al arrancar (los cambios hechos
  desde la consola de AWS aparecen ahí al reiniciar la app).

## Notas

- **Pedidos, reseñas, categorías, vendedores, ventas y alertas** van en MySQL y PostgreSQL, a los
  que Flask se conecta directo: ver [BASES_DE_DATOS.md](BASES_DE_DATOS.md).
- Con cualquier base real **no se crean los pedidos de ejemplo** de Ana (gastarían stock real y
  llenarían las tablas en cada arranque).

## Problemas comunes

| Mensaje | Causa |
| --- | --- |
| `Check that MARKETPLACE_API_KEY matches the Lambda` / `The product API rejected the key` | la clave de tu PC no coincide con la de la Lambda |
| HTTP 503 `MARKETPLACE_API_KEY is not configured` | falta la variable en la Lambda |
| `DynamoDB has no products yet` | falta el paso 5 |
| No puedo iniciar sesión con `ana@example.com` | faltan los usuarios del paso 5 o no pusiste `DATA_BACKEND=aws` |
| HTTP 500 o tiempo agotado | revisa CloudWatch (Lambda → Monitor → Logs): permisos del paso 3, nombre del índice o tiempo de espera |
| `AccessDeniedException` en los logs | la política del paso 3 tiene mal la región, la cuenta o el nombre de la tabla |
