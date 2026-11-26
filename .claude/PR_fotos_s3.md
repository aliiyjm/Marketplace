# Pull Request: feature/product-photos-s3 -> main

Abre este enlace (ya trae el título y la descripción) y da clic en **Create pull request**:

[Abrir el PR prellenado](https://github.com/aliiyjm/Marketplace/compare/main...feature/product-photos-s3?expand=1&title=Add+real+product+photos+stored+in+Amazon+S3&body=%23%23+Summary%0ASellers+can+upload%2C+replace+and+remove+a+product+photo+from+the+seller+center+%28%2A%2AAdd+product+%2F+Edit+product%2A%2A%29.+Products+without+a+photo+keep+the+generated+SVG+silhouette%2C+so+the+17%2C000+existing+products+are+unaffected.%0A%0A%23%23+How+it+works%0A1.+%2A%2AFlask%2A%2A+receives+the+upload+%28max+5+MB%29%2C+checks+the+real+file+type+by+its+first+bytes+and+%2A%2Are-encodes+it+with+Pillow%2A%2A%3A+max+1200+px+JPEG%2C+all+EXIF+metadata+%28GPS+location%2C+camera+data%29+removed.%0A2.+%2A%2ALambda%2A%2A+gets+the+clean+photo+on+the+new+route+%60POST+%2Fproductos%2Fimagen%60%2C+validates+it+again%2C+stores+it+in+%2A%2AS3%2A%2A+as+%60products%2F%3Cid%3E%2F%3Crandom%3E.jpg%60+and+writes+%60image_url%60+in+the+DynamoDB+item.+%60DELETE+%2Fproductos%2Fimagen%60+removes+it.%0A3.+Store%2C+cart%2C+checkout%2C+orders+and+seller+pages+show+%60%3Cimg%3E%60+when+%60image_url%60+exists%2C+otherwise+the+SVG.%0A%0A%23%23+Security%0A%7C+Risk+%7C+Defense+%7C%0A%7C---%7C---%7C%0A%7C+Script%2FHTML+disguised+as+an+image%2C+SVG+%7C+Magic-byte+check%3B+only+JPEG%2FPNG%2FWebP%2C+never+SVG+%28Flask+and+Lambda%29+%7C%0A%7C+Payload+hidden+inside+the+file+%28polyglot%29+%7C+Re-encoding+keeps+only+the+pixels+%7C%0A%7C+GPS+location+in+phone+photos+%7C+All+metadata+dropped+%7C%0A%7C+Decompression+bombs+%7C+Pixel+count+checked+before+decoding+%28max+40+MP%29+%7C%0A%7C+Huge+request+bodies+%7C+1+MB+everywhere%3B+6+MB+only+on+the+seller+product+form+and+only+with+a+session+%7C%0A%7C+A+seller+changing+another+seller%27s+photo+%7C+Ownership+check+%28seller_id+from+the+session%29+%7C%0A%7C+Guessing%2Foverwriting+photos+%7C+128-bit+random+name+generated+server+side+%7C%0A%7C+%60image_url%60+pointing+elsewhere+%2F+%60javascript%3A%60+%7C+Only+URLs+of+the+app%27s+own+bucket+%28or+%60%2Fmedia%2F%60+in+memory+mode%29+are+accepted+%7C%0A%7C+Images+from+any+origin+%7C+CSP+%60img-src%60+allows+only+the+bucket+in+%60PRODUCT_IMAGES_URL%60+%7C%0A%7C+Orphan+files+%7C+Old+photo+deleted+on+replace%3B+new+file+deleted+if+the+DynamoDB+write+fails+%7C%0A%0A%23%23+Configuration+needed+%28step+by+step+in+%60docs%2FFOTOS_S3.md%60%29%0A-+S3+bucket+with+public+%2A%2Aread-only%2A%2A+policy+on+%60products%2F%2A%60.%0A-+Lambda%3A+paste+the+regenerated+%60lambda_deploy%2Flambda_function.py%60%2C+add+env+var+%60MARKETPLACE_BUCKET%60.%0A-+API+Gateway%3A+add+%60POST%60+and+%60DELETE%60+%60%2Fproductos%2Fimagen%60+to+the+same+integration+%2817+routes%29.%0A-+%60.env%60%3A+%60PRODUCT_IMAGES_URL%3Dhttps%3A%2F%2F%3Cbucket%3E.s3.us-east-1.amazonaws.com%60%2C+then+%60pip+install+-r+requirements.txt%60+%28adds+Pillow%3B+Flask+%3E%3D+3.1+for+the+per-request+upload+limit%29.%0A%0AWithout+%60MARKETPLACE_BUCKET%60+the+app+keeps+working+and+shows+a+clear+message+when+a+seller+tries+to+upload.%0A%0A%23%23+Tests%0A%60python+-m+pytest+-q%60+-%3E+%2A%2A249+passed%2A%2A+%2861+new+in+%60tests%2Ftest_product_photos.py%60%29%3A+photo+cleaning%2C+malicious+files%2C+Lambda+route+against+the+layers+%2A%2Aand%2A%2A+the+generated+single+file+%28with+a+fake+S3+client%29%2C+Flask+upload%2Freplace%2Fremove%2C+size+limits%2C+ownership%2C+CSP%2C+and+the+full+Flask+-%3E+Lambda+-%3E+S3%2FDynamoDB+flow.+Also+checked+manually+in+memory+mode+in+the+browser.%0A%0ANot+yet+tested+against+a+real+S3+bucket+%28pending+the+AWS+steps+above%29.%0A%0A%F0%9F%A4%96+Generated+with+%5BClaude+Code%5D%28https%3A%2F%2Fclaude.com%2Fclaude-code%29)

Si el enlace largo no funciona, abre https://github.com/aliiyjm/Marketplace/compare/main...feature/product-photos-s3?expand=1 y pega el título y la descripción de abajo.

---

**Título:** Add real product photos stored in Amazon S3

**Descripción:**

## Summary
Sellers can upload, replace and remove a product photo from the seller center (**Add product / Edit product**). Products without a photo keep the generated SVG silhouette, so the 17,000 existing products are unaffected.

## How it works
1. **Flask** receives the upload (max 5 MB), checks the real file type by its first bytes and **re-encodes it with Pillow**: max 1200 px JPEG, all EXIF metadata (GPS location, camera data) removed.
2. **Lambda** gets the clean photo on the new route `POST /productos/imagen`, validates it again, stores it in **S3** as `products/<id>/<random>.jpg` and writes `image_url` in the DynamoDB item. `DELETE /productos/imagen` removes it.
3. Store, cart, checkout, orders and seller pages show `<img>` when `image_url` exists, otherwise the SVG.

## Security
| Risk | Defense |
|---|---|
| Script/HTML disguised as an image, SVG | Magic-byte check; only JPEG/PNG/WebP, never SVG (Flask and Lambda) |
| Payload hidden inside the file (polyglot) | Re-encoding keeps only the pixels |
| GPS location in phone photos | All metadata dropped |
| Decompression bombs | Pixel count checked before decoding (max 40 MP) |
| Huge request bodies | 1 MB everywhere; 6 MB only on the seller product form and only with a session |
| A seller changing another seller's photo | Ownership check (seller_id from the session) |
| Guessing/overwriting photos | 128-bit random name generated server side |
| `image_url` pointing elsewhere / `javascript:` | Only URLs of the app's own bucket (or `/media/` in memory mode) are accepted |
| Images from any origin | CSP `img-src` allows only the bucket in `PRODUCT_IMAGES_URL` |
| Orphan files | Old photo deleted on replace; new file deleted if the DynamoDB write fails |

## Configuration needed (step by step in `docs/FOTOS_S3.md`)
- S3 bucket with public **read-only** policy on `products/*`.
- Lambda: paste the regenerated `lambda_deploy/lambda_function.py`, add env var `MARKETPLACE_BUCKET`.
- API Gateway: add `POST` and `DELETE` `/productos/imagen` to the same integration (17 routes).
- `.env`: `PRODUCT_IMAGES_URL=https://<bucket>.s3.us-east-1.amazonaws.com`, then `pip install -r requirements.txt` (adds Pillow; Flask >= 3.1 for the per-request upload limit).

Without `MARKETPLACE_BUCKET` the app keeps working and shows a clear message when a seller tries to upload.

## Tests
`python -m pytest -q` -> **249 passed** (61 new in `tests/test_product_photos.py`): photo cleaning, malicious files, Lambda route against the layers **and** the generated single file (with a fake S3 client), Flask upload/replace/remove, size limits, ownership, CSP, and the full Flask -> Lambda -> S3/DynamoDB flow. Also checked manually in memory mode in the browser.

Not yet tested against a real S3 bucket (pending the AWS steps above).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
