# Stored procedures y transacciones atómicas

La rúbrica del **Proyecto B (Marketplace)** pide *"al menos 3 procedimientos almacenados (SP en RDS)
O 3 transacciones atómicas en DynamoDB (`TransactWriteItems`) ejecutadas desde Lambda"* para:

1. Creación de pedido y descuento transaccional de inventario.
2. Cancelación de pedido y reabastecimiento automático de stock.
3. Consulta e historial consolidado de ventas por usuario/vendedor.

El stock vive en **DynamoDB** y los pedidos en **MySQL**, así que el proyecto usa **las dos cosas**:
cada operación tiene su parte en la base que guarda esos datos, y la app las usa en su flujo normal.

| # | Operación | DynamoDB (Lambda, `TransactWriteItems`) | MySQL `finance_db` (SP) | PostgreSQL (función) |
|---|---|---|---|---|
| 1 | Crear pedido + descontar inventario | **Reserva**: `POST /productos/reservar` | `sp_create_order` | — |
| 2 | Cancelar + reabastecer | **Liberación**: `POST /productos/liberar` | `sp_cancel_order` | — |
| — | Pagar / enviar / entregar | — | `sp_change_order_status` | — |
| 3 | Historial consolidado | — | `sp_buyer_sales_history` (comprador) | `fn_seller_sales_history`, `fn_seller_units_by_day` (vendedor) |

Archivos:
- [`database/finance_procedures.sql`](../database/finance_procedures.sql): 4 procedimientos de MySQL.
- [`database/operations_procedures.sql`](../database/operations_procedures.sql): 2 funciones de PostgreSQL.
- [`Implementation/ProductServiceImplementation/ProductServiceImplementation.py`](../Implementation/ProductServiceImplementation/ProductServiceImplementation.py):
  `reserve_stock` y `release_stock` (las 2 transacciones de DynamoDB).

---

## 1. Crear pedido y descontar inventario

```
Checkout ──> Lambda POST /productos/reservar          (TransactWriteItems, todo o nada)
             ├─ Put    RESERVATION#<pedido> / STOCK    condición: attribute_not_exists(PK)
             ├─ Update PRODUCT#PRD-000001  stock-=2    condición: stock >= 2
             └─ Update PRODUCT#PRD-000002  stock-=1    condición: stock >= 1
         ──> MySQL CALL sp_create_order(...)            (START TRANSACTION ... COMMIT)
             orders + order_items + shipping_addresses + payments + order_status_history
             subtotal e IVA (16 %) calculados DENTRO del procedimiento
         ──> si MySQL falla: se libera la reserva (el stock regresa)
```

- **Sin condiciones de carrera:** si dos personas compran la última unidad, DynamoDB acepta solo una
  transacción; la otra no cambia **nada** (ni siquiera los otros productos de su carrito).
- **El número de pedido no se repite:** la reserva usa el número de pedido como llave, así que dos
  servidores no pueden usar el mismo número; el segundo pasa al siguiente.
- El mismo producto en varias líneas (tallas/colores) se suma en una sola operación, porque una
  transacción no puede tocar dos veces el mismo ítem.

## 2. Cancelar y reabastecer

```
Admin "Cancel" ──> MySQL CALL sp_cancel_order(pedido, estado_visto, fecha)
                   SELECT ... FOR UPDATE (bloquea el pedido) → valida → CANCELLED
                   pago PAID→REFUNDED / PENDING→CANCELLED → historial → devuelve las unidades
               ──> Lambda POST /productos/liberar     (TransactWriteItems)
                   ├─ Update RESERVATION#<pedido>  RESERVED→RELEASED  condición: status = RESERVED
                   └─ Update PRODUCT#...           stock += unidades
```

- Si dos admins cancelan a la vez, el bloqueo de fila hace que **solo uno** gane (el otro recibe
  "the order changed meanwhile"), y la condición `status = RESERVED` impide reabastecer dos veces.
- Pedidos creados **antes** de este cambio no tienen reserva: su stock se devuelve producto por producto.

## 3. Historial consolidado

- **Comprador** (`sp_buyer_sales_history`): una fila por pedido (líneas, unidades) más los totales del
  comprador calculados con *window functions*: pedidos, pedidos no cancelados, dinero gastado y
  unidades. Se ve en **My orders** (tarjetas "Orders / Total spent / Items bought").
- **Vendedor** (`fn_seller_sales_history`): por producto, unidades, días con venta, primera y última
  venta. Se ve en **Seller center → Best-selling products**. `fn_seller_units_by_day` alimenta la gráfica.

## Seguridad de los procedimientos

- Todo valor llega como **parámetro**; no hay SQL dinámico (`PREPARE`/`EXECUTE`): no hay inyección SQL.
- Validan sus entradas aunque la app ya lo haga (cantidades 1-10, máximo 30 líneas, código postal,
  solo los **últimos 4 dígitos** y el **token** `tok_...` de la tarjeta, método de pago válido).
- Errores de negocio con `SIGNAL SQLSTATE '45000'` y un código (`STALE_ORDER`, `INVALID_TRANSITION`...)
  que la app traduce a un mensaje amable. Cualquier otro error hace `ROLLBACK` completo.
- Las rutas de la Lambda exigen la API key, validan el número de pedido (solo dígitos), los
  productos y las cantidades.

---

## Pasos para activarlo (una vez)

> **Orden importante:** la app nueva necesita los procedimientos **y** la Lambda nueva. Haz los 3
> pasos antes de usarla con tus bases reales.

1. **Enciende las RDS** (RDS → Databases → Actions → Start) y espera a que estén *Available*.
2. **Instala los procedimientos** (no borra datos; se puede repetir):
   ```bash
   python scripts/install_procedures.py
   ```
   Pregunta antes de hacer nada; contesta `y`. Debe terminar en `Done.`
3. **Actualiza la Lambda y API Gateway:**
   1. `python scripts/build_single_file_lambda.py`
   2. Lambda → Code → pega `lambda_deploy/lambda_function.py` → **Deploy**.
   3. API Gateway → Routes → **Create** `POST /productos/reservar` y `POST /productos/liberar`.
   4. A cada una: **Attach integration** → la misma integración (tu Lambda). Si la etapa no tiene
      auto-deploy, **Deploy**.
4. **Verifica:**
   ```bash
   python scripts/check_databases.py
   ```
   Debe mostrar `✓ stored procedures installed` en MySQL y PostgreSQL,
   `✓ stock POST /productos/liberar (atomic transactions)` y terminar en `All good.`

### Probar los procedimientos en el servidor real (opcional, para la demostración)

```bash
python scripts/verify_procedures_live.py
```

Crea una base de datos **temporal** `sp_test_<aleatorio>` en MySQL y un esquema temporal en
PostgreSQL, crea ahí las tablas y los procedimientos, prueba todos los casos (incluidas dos
cancelaciones simultáneas) y **borra lo temporal** al terminar. Tus datos no se leen ni se cambian.

### Pruebas automatizadas

```bash
python -m pytest -q
```

`tests/test_atomic_transactions.py` prueba las transacciones contra el código por capas **y** contra
el archivo único de la Lambda; `tests/test_stored_procedures.py` prueba el contrato, que la app haga
`CALL` con parámetros y la traducción de errores. SQLite (usado en las pruebas) no tiene
procedimientos: ahí las mismas reglas corren como SQL normal, por eso existe también
`verify_procedures_live.py`.
