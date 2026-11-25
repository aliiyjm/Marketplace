# Fotos reales de producto con Amazon S3

El vendedor sube una foto desde **Seller center → Add product / Edit product**. Si un producto
no tiene foto, se sigue mostrando la silueta SVG pintada con su color (como antes).

## Cómo funciona

```
Navegador ──(formulario, máx. 5 MB)──> Flask
   Flask: valida el tipo real del archivo, vuelve a codificar con Pillow
          (máx. 1200 px, JPEG, SIN metadatos EXIF/GPS) ──> POST /productos/imagen (x-api-key)
   Lambda: vuelve a validar (base64, ≤ 1.5 MB, tipo real) ──> S3: products/PRD-000123/<aleatorio>.jpg
           y guarda image_url en el producto de DynamoDB
Navegador <──(<img src="https://<bucket>.s3...">)── lee la foto directo de S3 (solo lectura pública)
```

Seguridad aplicada:

| Riesgo | Defensa |
| --- | --- |
| Subir un script o HTML disfrazado de imagen | Se revisan los primeros bytes (no el nombre ni el Content-Type); solo JPEG, PNG y WebP; **nunca SVG** |
| Código escondido dentro de la imagen (archivo "políglota") | La foto se vuelve a codificar: solo se guardan los píxeles |
| Fotos de celular con la **ubicación GPS** del vendedor | Se borran todos los metadatos EXIF |
| "Bomba de descompresión" (archivo chico que ocupa GB en memoria) | Se cuentan los píxeles antes de decodificar (máx. 40 MP) |
| Cuerpos enormes para tumbar el servidor | 1 MB en toda la app; 6 MB solo en el formulario de producto y solo con sesión iniciada |
| Que un vendedor cambie la foto de otro | Se verifica que el producto sea del vendedor de la sesión |
| Sobrescribir o adivinar fotos ajenas | Nombre aleatorio de 128 bits generado en el servidor |
| Que `image_url` apunte a otro sitio o a `javascript:` | Solo se aceptan URLs de **tu** bucket (o `/media/` en modo demo) |
| Cargar imágenes de cualquier sitio | La CSP solo permite imágenes de la app y del bucket de `PRODUCT_IMAGES_URL` |
| Que el bucket quede abierto | Público **solo para leer** `products/*`; subir y borrar solo lo hace la Lambda |

> Nota: las fotos de productos en borrador también están en el bucket (con nombre aleatorio
> imposible de adivinar). No subas fotos con información privada.

---

## Pasos en AWS (una sola vez)

### 1. Crear el bucket

1. Consola de AWS → busca **S3** → **Create bucket**.
2. **Bucket name:** un nombre único en todo AWS, solo minúsculas, números y guiones (sin puntos).
   Ejemplo: `marketplace-fotos-utez-2026`. Anótalo.
3. **AWS Region:** la misma de tu Lambda (`us-east-1`).
4. **Object Ownership:** deja **ACLs disabled (recommended)**.
5. **Block Public Access settings for this bucket:**
   - Quita la palomita de **Block all public access**.
   - Vuelve a marcar las **dos primeras** casillas (las que hablan de *ACLs*). Deben quedar
     **desmarcadas** solo las dos que hablan de *bucket policies*.
   - Marca **I acknowledge that the current settings might result in this bucket and the objects
     within becoming public.**
6. Lo demás déjalo igual → **Create bucket**.

### 2. Permitir solo la lectura pública de las fotos

1. Abre tu bucket → pestaña **Permissions** → **Bucket policy** → **Edit**.
2. Pega esto y cambia `TU-BUCKET` por el nombre del paso 1:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "PublicReadProductPhotos",
      "Effect": "Allow",
      "Principal": "*",
      "Action": "s3:GetObject",
      "Resource": "arn:aws:s3:::TU-BUCKET/products/*"
    }
  ]
}
```

3. **Save changes**. Esta política solo deja **leer** archivos de la carpeta `products/`;
   nadie puede listar, subir ni borrar.

### 3. Actualizar la Lambda

1. En tu computadora, genera el archivo único (ya viene generado en la rama, pero así te aseguras):
   ```bash
   python scripts/build_single_file_lambda.py
   ```
2. Consola → **Lambda** → tu función → pestaña **Code** → abre `lambda_function.py`,
   borra todo y pega el contenido de `lambda_deploy/lambda_function.py` → **Deploy**.
3. **Configuration → Environment variables → Edit → Add environment variable:**

   | Key | Value |
   | --- | --- |
   | `MARKETPLACE_BUCKET` | el nombre del bucket (paso 1), por ejemplo `marketplace-fotos-utez-2026` |

   **Save**. No pongas `https://` ni `/` aquí: solo el nombre.
4. Permisos: en AWS Academy la Lambda usa **LabRole**, que ya puede escribir en S3. No hace
   falta (ni se puede) crear políticas IAM.

### 4. Agregar las 2 rutas nuevas en API Gateway

1. Consola → **API Gateway** → tu API (HTTP API) → **Routes** → **Create**.
2. Crea `POST` `/productos/imagen` → **Create**.
3. Crea `DELETE` `/productos/imagen` → **Create**.
4. Para **cada una**: selecciónala → **Attach integration** → elige la **misma integración** que
   usan las otras 15 rutas (tu Lambda) → **Attach integration**.
5. Si tu etapa no tiene *auto-deploy*: **Deploy** → elige la etapa → **Deploy**.

Ahora son **17 rutas**, todas a la misma Lambda.

### 5. Configurar la app (tu `.env`)

Agrega esta línea a tu `.env` (cambia el nombre del bucket; sin `/` al final):

```
PRODUCT_IMAGES_URL=https://marketplace-fotos-utez-2026.s3.us-east-1.amazonaws.com
```

E instala Pillow (la librería que limpia las fotos):

```bash
pip install -r requirements.txt
```

### 6. Probar

1. `python View/MainViews/PY/app.py` → entra como vendedor (`carlos@urbanthread.mx`).
2. **Seller center → My products → Edit** en cualquier producto → **Upload a photo** → **Save changes**.
3. Debe decir **Changes saved.** y la miniatura aparece en el formulario y en la tienda.
4. En S3 → tu bucket → `products/PRD-.../` debe aparecer un `.jpg` con nombre aleatorio.
5. Para quitarla: **Edit** → marca **Remove the photo** → **Save changes** (se borra también de S3).

---

## Si algo falla

| Síntoma | Causa / solución |
| --- | --- |
| `The product was saved, but the photo was not: Photos are not enabled yet: set MARKETPLACE_BUCKET in the Lambda.` | Falta la variable `MARKETPLACE_BUCKET` en la Lambda (paso 3.3) o no hiciste **Deploy** del código nuevo |
| `... the photo was not: The product database answered with an error (HTTP 404).` | Faltan las rutas `/productos/imagen` o su integración (paso 4) |
| `... (HTTP 500)` | Revisa **CloudWatch** de la Lambda. Si dice `AccessDenied` en `PutObject`, el rol no puede escribir en ese bucket; si dice `NoSuchBucket`, el nombre de `MARKETPLACE_BUCKET` está mal |
| La foto se guarda pero en la página aparece rota | Falta `PRODUCT_IMAGES_URL` en el `.env` o no coincide con el bucket (la CSP la bloquea), o falta la política del paso 2 / el bloqueo de acceso público del paso 1.5 |
| `The photo must be 5 MB or less.` / `Upload a JPG, PNG or WebP photo.` | El archivo no cumple las reglas: usa otro |
