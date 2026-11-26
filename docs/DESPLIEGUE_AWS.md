# Despliegue en AWS (Learner Lab) con un script

`scripts/aws_deploy.py` deja la parte de AWS lista sin dar clics en la consola. Usa las
**credenciales temporales** del Learner Lab (no crea roles ni políticas IAM: la Lambda sigue con `LabRole`).

| Hace | Detalle |
|---|---|
| API Gateway | Crea las rutas que falten (19) en **la misma** integración de la Lambda; despliega la etapa si no tiene auto-deploy |
| Lambda | Sube `lambda_deploy/lambda_function.py`, memoria **256 MB** (límite del lab 128-256), timeout 30 s, agrega `MARKETPLACE_BUCKET` **sin tocar** las demás variables; solo tu API puede invocarla |
| S3 | Crea el bucket de fotos, lectura pública **solo** de `products/*`, ACLs bloqueadas |
| `.env` | Escribe `PRODUCT_IMAGES_URL` (la CSP lo necesita) |
| Revisión | Modo de capacidad de DynamoDB, clase/almacenamiento/estado de las RDS (`db.t3.micro`/`db.t4g.micro`, ≤ 20 GB) |

Es **idempotente**: si lo corres otra vez, solo cambia lo que falte. Nunca muestra llaves ni contraseñas.

## 1. Credenciales del Learner Lab (cada vez que inicias el lab)

1. AWS Academy → tu curso → **Learner Lab** → **Start Lab** y espera el punto verde.
2. **AWS Details** → junto a *AWS CLI* da clic en **Show**.
3. Copia el bloque completo (`[default]`, `aws_access_key_id`, `aws_secret_access_key`, `aws_session_token`).
4. Pégalo en el archivo `C:\Users\<tu usuario>\.aws\credentials` (crea la carpeta `.aws` si no existe)
   y guárdalo. **Nunca** lo pongas en el proyecto ni lo subas a GitHub.

Las credenciales caducan al terminar la sesión del lab: repite estos pasos la próxima vez.

## 2. Revisar y aplicar

```bash
pip install -r requirements.txt
python scripts/aws_deploy.py
python scripts/aws_deploy.py --apply
```

El primero solo **revisa** y lista lo que cambiaría (•). El segundo lo aplica (→).
Con `--start-rds` también enciende las RDS apagadas.

## 3. Procedimientos y verificación

```bash
python scripts/install_procedures.py
python scripts/check_databases.py
```

Al terminar de trabajar, **apaga las RDS** (RDS → Actions → Stop temporarily) para no gastar crédito.
