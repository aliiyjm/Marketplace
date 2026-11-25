"""ProductService: Lambda routes for the product catalog.

    API Gateway -> LambdaRouter -> handle_productos (routing + auth + validation)
                -> ProductServiceImplementation (DynamoDB MarketplaceDB)

Routes:
    GET    /productos/consultar   ?product_id=PRD-000001         one product
                                  ?limit=1000&cursor=...&category=  page of products
    POST   /productos/agregar     body: product                   create
    PUT    /productos/modificar   body: product                   replace
    DELETE /productos/eliminar    ?product_id=PRD-000001          soft delete
    POST   /productos/stock       body: {product_id, delta}       atomic stock change
    POST   /productos/cargar      body: {items: [...]} (max 100)  initial bulk load
    POST   /productos/imagen      body: {product_id, content_type, data (base64)}  set photo (S3)
    DELETE /productos/imagen      ?product_id=PRD-000001         remove photo

Security: every route that WRITES requires the header `x-api-key` equal to the
Lambda variable MARKETPLACE_API_KEY (constant-time compare). If it is not
configured, writes are refused (fail closed). Reads without the key only see
ACTIVE products (drafts and deleted ones stay private). Every payload is
validated with ProductItem before it reaches DynamoDB, and errors never expose
internal details. Photos are checked again here (defense in depth): strict
base64, at most MAX_IMAGE_BYTES, and the real file type (magic bytes) must be
JPEG/PNG/WebP and match the declared one.
"""
import base64
import binascii
import os

from Implementation.ProductServiceImplementation.ProductImageStore import S3ImageStore
from Implementation.ProductServiceImplementation.ProductServiceImplementation import \
    ProductServiceImplementation
from Interfaces.ProductServiceInterface.ProductDTO import (IMAGE_TYPES, MAX_IMAGE_BYTES, ProductItem,
                                                           image_type)
from Interfaces.ProductServiceInterface.ProductServiceInterface import (ImagesDisabled, OutOfStock,
                                                                        ProductExists, ProductNotFound)
from Interfaces.Validators import PRODUCT_ID_RE, SLUG_RE, ValidationError, parse_int
from Services.LambdaCommon import (api_key_configured, authorized, get_table, gsi_name,
                                   json_body, log, parse_request, response)

MAX_BATCH = 100
MAX_IMAGE_BODY = MAX_IMAGE_BYTES * 4 // 3 + 4096      # base64 makes the file ~33% bigger
PRODUCT_QUERY = "/productos/consultar"
PRODUCT_ADD = "/productos/agregar"
PRODUCT_UPDATE = "/productos/modificar"
PRODUCT_DELETE = "/productos/eliminar"
PRODUCT_STOCK = "/productos/stock"
PRODUCT_LOAD = "/productos/cargar"
PRODUCT_IMAGE = "/productos/imagen"
PRODUCT_ROUTES = {
    (PRODUCT_QUERY, "GET"), (PRODUCT_ADD, "POST"), (PRODUCT_UPDATE, "PUT"),
    (PRODUCT_DELETE, "DELETE"), (PRODUCT_STOCK, "POST"), (PRODUCT_LOAD, "POST"),
    (PRODUCT_IMAGE, "POST"), (PRODUCT_IMAGE, "DELETE"),
}
PRODUCT_WRITE_ROUTES = {PRODUCT_ADD, PRODUCT_UPDATE, PRODUCT_DELETE, PRODUCT_STOCK, PRODUCT_LOAD, PRODUCT_IMAGE}

_product_service = None


def default_image_store():
    """S3 bucket from MARKETPLACE_BUCKET (AWS_REGION is set by Lambda itself), or None."""
    bucket = os.environ.get("MARKETPLACE_BUCKET", "").strip()
    if not bucket:
        return None
    try:
        return S3ImageStore(bucket, os.environ.get("AWS_REGION", "us-east-1"))
    except ValueError as e:
        log.error("Photos disabled: %s", e)
        return None


def default_product_service():
    global _product_service
    if _product_service is None:
        _product_service = ProductServiceImplementation(get_table(), gsi_name(), images=default_image_store())
    return _product_service


def _photo(data):
    """Decode and check an uploaded photo: (bytes, content_type)."""
    content_type = data.get("content_type")
    if content_type not in IMAGE_TYPES:
        raise ValidationError("content_type must be image/jpeg, image/png or image/webp.")
    raw = data.get("data")
    if not isinstance(raw, str) or not raw or len(raw) > MAX_IMAGE_BODY:
        raise ValidationError(f"data must be the photo in base64 (at most {MAX_IMAGE_BYTES // 1000} KB).")
    try:
        content = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        raise ValidationError("data is not valid base64.") from None
    if len(content) > MAX_IMAGE_BYTES:
        raise ValidationError(f"The photo is larger than {MAX_IMAGE_BYTES // 1000} KB.")
    if image_type(content) != content_type:
        raise ValidationError("The file is not the image type it claims to be.")
    return content, content_type


def _product_id(value):
    value = str(value or "")
    if not PRODUCT_ID_RE.fullmatch(value):
        raise ValidationError("product_id must look like PRD-000123.")
    return value


def handle_productos(event, context=None, service=None):
    method, path, headers, query, body = parse_request(event)
    route = next((r for r, _ in PRODUCT_ROUTES if path.endswith(r)), None)
    if route is None:
        return response(404, {"error": "Route not found."})
    if (route, method) not in PRODUCT_ROUTES:
        return response(405, {"error": "Method not allowed."})
    trusted = authorized(headers)
    if route in PRODUCT_WRITE_ROUTES:
        if not api_key_configured():
            return response(503, {"error": "Writes are disabled: MARKETPLACE_API_KEY is not configured in the Lambda."})
        if not trusted:
            return response(403, {"error": "Forbidden."})

    svc = service or default_product_service()
    try:
        if route == PRODUCT_QUERY:
            if query.get("product_id"):
                item = svc.get(_product_id(query["product_id"]))
                if not item or (not trusted and item.status != "active"):
                    return response(404, {"error": "Product not found."})
                return response(200, item.to_payload())
            limit = parse_int(query.get("limit", 1000), field="limit", min_value=1, max_value=2000, strict=True)
            category = query.get("category") or None
            if category and not (len(category) <= 60 and SLUG_RE.fullmatch(category)):
                raise ValidationError("category is invalid.")
            items, cursor = svc.list_page(query.get("cursor"), limit, category)
            if not trusted:
                items = [i for i in items if i.status == "active"]
            return response(200, {"items": [i.to_payload() for i in items], "next_cursor": cursor})

        if route == PRODUCT_DELETE:
            svc.soft_delete(_product_id(query.get("product_id")))
            return response(200, {"deleted": query.get("product_id")})

        if route == PRODUCT_IMAGE:
            if method == "DELETE":
                product_id = _product_id(query.get("product_id"))
                svc.remove_image(product_id)
                return response(200, {"product_id": product_id, "image_url": ""})
            data = json_body(body, max_size=MAX_IMAGE_BODY)
            product_id = _product_id(data.get("product_id"))
            content, content_type = _photo(data)
            url = svc.set_image(product_id, content, content_type)
            return response(200, {"product_id": product_id, "image_url": url})

        data = json_body(body)
        if route == PRODUCT_ADD:
            item = ProductItem.from_payload(data)
            svc.create(item)
            return response(201, {"product_id": item.product_id})
        if route == PRODUCT_UPDATE:
            item = ProductItem.from_payload(data)
            svc.replace(item)
            return response(200, {"product_id": item.product_id})
        if route == PRODUCT_STOCK:
            delta = data.get("delta")
            if not isinstance(delta, int) or isinstance(delta, bool):
                raise ValidationError("delta must be a whole number.")
            stock = svc.adjust_stock(_product_id(data.get("product_id")), delta)
            return response(200, {"product_id": data["product_id"], "stock": stock})
        if route == PRODUCT_LOAD:
            raw = data.get("items")
            if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_BATCH:
                raise ValidationError(f"items must be a list of 1 to {MAX_BATCH} products.")
            items = []
            for index, entry in enumerate(raw):
                try:
                    items.append(ProductItem.from_payload(entry))
                except ValidationError as e:
                    raise ValidationError([f"items[{index}]: {msg}" for msg in e.errors]) from None
            return response(200, {"written": svc.batch_load(items)})
    except OutOfStock as e:
        return response(409, {"error": str(e), "code": "OUT_OF_STOCK"})
    except ProductExists as e:
        return response(409, {"error": str(e), "code": "EXISTS"})
    except ProductNotFound as e:
        return response(404, {"error": str(e), "code": "NOT_FOUND"})
    except ImagesDisabled as e:
        return response(503, {"error": str(e), "code": "IMAGES_DISABLED"})
    except ValidationError as e:
        return response(400, {"error": "; ".join(e.errors[:10])})
    except Exception:  # never leak internals to the caller; details go to CloudWatch
        log.exception("Unhandled error on %s %s", method, route)
        return response(500, {"error": "Internal error."})
    return response(404, {"error": "Route not found."})
