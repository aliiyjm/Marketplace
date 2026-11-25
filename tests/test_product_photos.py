"""Product photos: upload from the seller center, cleaning (re-encode, no EXIF),
the Lambda route that stores them in S3, and the security limits around them.

S3 is simulated with FakeS3Client (the real S3ImageStore runs on top of it), so
these tests never touch AWS."""
import base64
import importlib.util
import io
import json
import random
import re

import pytest
from PIL import Image

from Implementation.DataSource.ProductPhotos import MAX_SIDE, prepare_photo
from Implementation.ProductServiceImplementation.ProductImageStore import S3ImageStore
from Implementation.ProductServiceImplementation.ProductServiceImplementation import \
    ProductServiceImplementation
from Interfaces.ProductServiceInterface.ProductDTO import MAX_IMAGE_BYTES, ProductItem
from Interfaces.ProductServiceInterface.ProductServiceInterface import ImagesDisabled
from Interfaces.Validators import ValidationError
from scripts.build_single_file_lambda import OUT as SINGLE_FILE
from scripts.load_to_dynamo import build_payloads, upload
from Services.ProductService.ProductService import MAX_IMAGE_BODY, default_image_store
from Services.ProductService.ProductService import handle_productos as lambda_handler
from Services.ServiceContainer import build_services
from tests.conftest import CARLOS_SELLER, login, some_product, token
from tests.fake_dynamo import FakeS3Client, FakeTable, LambdaHttp
from View.MainViews.PY.app import create_app
from View.MainViews.Utils.product_api import ProductApiClient

KEY = "photo-key-123"
BUCKET = "test-bucket"
BASE = f"https://{BUCKET}.s3.us-east-1.amazonaws.com/"
PAYLOADS = build_payloads(1200)
URL_RE = re.compile(re.escape(BASE) + r"products/PRD-\d{6}/[0-9a-f]{32}\.jpg")


def _load_single():
    spec = importlib.util.spec_from_file_location("photos_single_file", SINGLE_FILE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SINGLE = _load_single()


# ---------------- sample files ----------------
def image_bytes(fmt="JPEG", size=(800, 600), mode="RGB", color=(200, 30, 60), exif=None):
    img = Image.new(mode, size, color)
    out = io.BytesIO()
    kwargs = {"exif": exif} if exif is not None else {}
    img.save(out, fmt, **kwargs)
    return out.getvalue()


def exif_with_gps_and_orientation(orientation=1):
    exif = Image.Exif()
    exif[0x0112] = orientation                      # Orientation
    exif[0x010F] = "PhoneMaker"                     # Make
    exif[0x8825] = {1: "N", 2: (18.0, 55.0, 12.0), 3: "W", 4: (99.0, 14.0, 3.0)}   # GPS
    return exif


def noisy_png(side=1100):
    """Random pixels compress badly: a PNG of ~3.5 MB (bigger than the 1 MB form limit)."""
    rnd = random.Random(7)
    img = Image.frombytes("RGB", (side, side), bytes(rnd.getrandbits(8) for _ in range(side * side * 3)))
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


@pytest.fixture(autouse=True)
def api_key(monkeypatch):
    monkeypatch.setenv("MARKETPLACE_API_KEY", KEY)


# ================= cleaning (Flask side) =================
def test_photo_is_reencoded_small_and_without_metadata():
    raw = image_bytes(size=(3000, 2000), exif=exif_with_gps_and_orientation())
    assert b"PhoneMaker" in raw                                   # the original has metadata
    data, content_type = prepare_photo(raw)
    assert content_type == "image/jpeg" and data[:3] == b"\xff\xd8\xff"
    img = Image.open(io.BytesIO(data))
    assert max(img.size) == MAX_SIDE and img.size == (1200, 800)
    assert len(img.getexif()) == 0 and b"Exif" not in data and b"PhoneMaker" not in data
    assert len(data) <= MAX_IMAGE_BYTES


def test_orientation_is_applied_before_metadata_is_dropped():
    raw = image_bytes(size=(400, 200), exif=exif_with_gps_and_orientation(orientation=6))   # rotated phone photo
    img = Image.open(io.BytesIO(prepare_photo(raw)[0]))
    assert img.size == (200, 400)


def test_transparent_png_and_webp_become_jpeg():
    png = image_bytes("PNG", mode="RGBA", color=(0, 0, 0, 0))
    img = Image.open(io.BytesIO(prepare_photo(png)[0]))
    assert img.format == "JPEG" and img.getpixel((10, 10)) == (255, 255, 255)   # white background
    assert prepare_photo(image_bytes("WEBP"))[1] == "image/jpeg"


def test_hidden_payload_after_the_image_is_removed():
    raw = image_bytes("PNG") + b"<script>alert(document.cookie)</script>"
    data, _ = prepare_photo(raw)
    assert b"<script>" not in data


@pytest.mark.parametrize("raw, message", [
    (b"", "empty"),
    (b"just some text", "JPG, PNG or WebP"),
    (b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>', "JPG, PNG or WebP"),
    (image_bytes("GIF", mode="P"), "JPG, PNG or WebP"),
    (b"\xff\xd8\xff" + b"not really a jpeg" * 10, "could not be read"),
    (image_bytes("PNG")[:120], "could not be read"),                # truncated
    (b"\xff\xd8\xff" + b"0" * (5 * 1024 * 1024), "5 MB"),
])
def test_bad_files_are_rejected(raw, message):
    with pytest.raises(ValidationError) as e:
        prepare_photo(raw)
    assert message in str(e.value)


def test_decompression_bomb_is_rejected_before_decoding():
    img = Image.new("1", (10000, 5000))                           # 50 MP, the file is only a few KB
    out = io.BytesIO()
    img.save(out, "PNG")
    assert len(out.getvalue()) < 100_000
    with pytest.raises(ValidationError, match="too many pixels"):
        prepare_photo(out.getvalue())


# ================= Lambda route (S3 + DynamoDB) =================
@pytest.fixture
def table():
    return FakeTable()


@pytest.fixture(params=["layers", "single_file"])
def lam(request, table):
    """Each Lambda test runs against the layers and the generated single file."""
    s3 = FakeS3Client()
    if request.param == "layers":
        service = ProductServiceImplementation(table, images=S3ImageStore(BUCKET, "us-east-1", client=s3))
        service._handler = lambda_handler
    else:
        service = SINGLE.ProductServiceImplementation(table, images=SINGLE.S3ImageStore(BUCKET, "us-east-1", client=s3))
        service._handler = SINGLE.handle_productos
    service.s3 = s3
    assert call(service, "POST", "/productos/agregar", body=PAYLOADS[0])[0] == 201
    return service


def event(method, path, *, query=None, body=None, key=KEY):
    return {"rawPath": path, "requestContext": {"http": {"method": method}},
            "headers": {"x-api-key": key} if key else {}, "queryStringParameters": query,
            "body": json.dumps(body) if body is not None else None}


def call(service, *args, **kwargs):
    out = service._handler(event(*args, **kwargs), service=service)
    return out["statusCode"], json.loads(out["body"])


def photo_body(data=None, content_type="image/jpeg", product_id="PRD-000001"):
    data = prepare_photo(image_bytes())[0] if data is None else data
    return {"product_id": product_id, "content_type": content_type, "data": base64.b64encode(data).decode()}


def stored(table, pid="PRD-000001"):
    return table.items[(f"PRODUCT#{pid}", "METADATA")]


def test_photo_routes_need_the_api_key(lam):
    assert call(lam, "POST", "/productos/imagen", body=photo_body(), key="wrong")[0] == 403
    assert call(lam, "DELETE", "/productos/imagen", query={"product_id": "PRD-000001"}, key=None)[0] == 403
    assert lam.s3.objects == {}


def test_upload_goes_to_s3_and_the_url_to_dynamodb(lam, table):
    status, body = call(lam, "POST", "/productos/imagen", body=photo_body())
    assert status == 200 and URL_RE.fullmatch(body["image_url"])
    assert stored(table)["image_url"] == body["image_url"]
    key = body["image_url"][len(BASE):]
    obj = lam.s3.objects[(BUCKET, key)]
    assert obj["ContentType"] == "image/jpeg" and "immutable" in obj["CacheControl"]
    status, product = call(lam, "GET", "/productos/consultar", query={"product_id": "PRD-000001"}, key=None)
    assert product["image_url"] == body["image_url"]


def test_new_photo_replaces_the_old_one_and_delete_removes_it(lam, table):
    first = call(lam, "POST", "/productos/imagen", body=photo_body())[1]["image_url"]
    second = call(lam, "POST", "/productos/imagen", body=photo_body())[1]["image_url"]
    assert first != second and list(lam.s3.objects) == [(BUCKET, second[len(BASE):])]   # old file deleted
    assert call(lam, "DELETE", "/productos/imagen", query={"product_id": "PRD-000001"})[0] == 200
    assert "image_url" not in stored(table) and lam.s3.objects == {}
    assert call(lam, "DELETE", "/productos/imagen", query={"product_id": "PRD-000001"})[0] == 200   # idempotent


@pytest.mark.parametrize("change", [
    {"content_type": "image/svg+xml"},
    {"content_type": "image/gif"},
    {"data": "!!! not base64 !!!"},
    {"data": base64.b64encode(b"<html>hello</html>").decode()},                    # not an image
    {"data": base64.b64encode(image_bytes("PNG")).decode()},                       # PNG declared as JPEG
    {"data": base64.b64encode(b"\xff\xd8\xff" + b"0" * MAX_IMAGE_BYTES).decode()},  # too big
    {"product_id": "../../etc"},
])
def test_bad_uploads_are_rejected(lam, table, change):
    status, body = call(lam, "POST", "/productos/imagen", body=dict(photo_body(), **change))
    assert status == 400 and body["error"]
    assert lam.s3.objects == {} and "image_url" not in stored(table)


def test_photo_for_a_missing_product(lam):
    assert call(lam, "POST", "/productos/imagen", body=photo_body(product_id="PRD-999999"))[0] == 404
    assert lam.s3.objects == {}


def test_failed_dynamodb_write_deletes_the_uploaded_file(lam, table, monkeypatch):
    def broken(**kwargs):
        raise RuntimeError("DynamoDB is down")
    monkeypatch.setattr(table, "update_item", broken)
    status, body = call(lam, "POST", "/productos/imagen", body=photo_body())
    assert status == 500 and body == {"error": "Internal error."}         # no internal details leak
    assert lam.s3.objects == {}                                            # no orphan file


def test_photos_disabled_without_a_bucket(table):
    service = ProductServiceImplementation(table)
    service._handler = lambda_handler
    call(service, "POST", "/productos/agregar", body=PAYLOADS[0])
    status, body = call(service, "POST", "/productos/imagen", body=photo_body())
    assert status == 503 and body["code"] == "IMAGES_DISABLED"


def test_default_image_store_reads_the_lambda_variables(monkeypatch):
    monkeypatch.delenv("MARKETPLACE_BUCKET", raising=False)
    assert default_image_store() is None
    monkeypatch.setenv("MARKETPLACE_BUCKET", "Bad.Bucket_Name")
    assert default_image_store() is None                                    # invalid: disabled, logged
    monkeypatch.setenv("MARKETPLACE_BUCKET", "marketplace-photos")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    assert default_image_store().base_url == "https://marketplace-photos.s3.us-east-1.amazonaws.com/"
    assert MAX_IMAGE_BODY < 6_000_000                                       # Lambda payload limit


def test_store_never_touches_other_buckets_or_paths():
    s3 = FakeS3Client()
    store = S3ImageStore(BUCKET, "us-east-1", client=s3)
    good = BASE + "products/PRD-000001/" + "a" * 32 + ".jpg"
    assert store.key_of(good) == "products/PRD-000001/" + "a" * 32 + ".jpg"
    assert store.key_of("https://other.s3.us-east-1.amazonaws.com/products/PRD-000001/" + "a" * 32 + ".jpg") is None
    assert store.key_of(BASE + "index.html") is None
    s3.objects[(BUCKET, "index.html")] = {"Body": b"site"}
    store.delete("index.html")
    store.delete("../index.html")
    assert (BUCKET, "index.html") in s3.objects
    with pytest.raises(ValueError):
        store.put("index.html", b"x", "text/html")


@pytest.mark.parametrize("url", [
    "javascript:alert(1)",
    "https://evil.example.com/products/PRD-000001/" + "a" * 32 + ".jpg",
    BASE + "other/PRD-000001/" + "a" * 32 + ".jpg",
    "/media/../../.env",
    "http://test-bucket.s3.us-east-1.amazonaws.com/products/PRD-000001/" + "a" * 32 + ".jpg",
])
def test_image_url_must_be_a_photo_of_this_app(lam, url):
    status, body = call(lam, "PUT", "/productos/modificar", body=dict(PAYLOADS[0], image_url=url))
    assert status == 400 and "image_url" in body["error"]


def test_valid_image_url_survives_a_product_update(lam, table):
    url = call(lam, "POST", "/productos/imagen", body=photo_body())[1]["image_url"]
    product = ProductItem.from_item(stored(table)).to_payload()
    assert call(lam, "PUT", "/productos/modificar", body=dict(product, price="123.00"))[0] == 200
    assert stored(table)["image_url"] == url


# ================= Flask app, memory mode =================
def product_form(**over):
    data = {"name": "Linen Shirt", "description": "Fresh", "category_id": "22", "price": "599.00",
            "stock": "12", "threshold_limit": "5", "sizes": "S, M, L", "colors": "White, Navy",
            "visible": "1", "attr_key": ["Material"], "attr_value": ["Linen"]}
    data.update(over)
    return data


def post_form(client, url, photo=None, filename="photo.jpg", **over):
    data = dict(product_form(**over), csrf_token=token(client, url))
    if photo is not None:
        data["photo"] = (io.BytesIO(photo), filename)
    return client.post(url, data=data, content_type="multipart/form-data")


def newest(services):
    return max(services.store.products.values(), key=lambda p: p["product_id"])


def test_seller_uploads_a_big_photo_and_the_store_shows_it(client, services):
    login(client, "carlos@urbanthread.mx")
    raw = noisy_png()
    assert 1024 * 1024 < len(raw) < 5 * 1024 * 1024          # bigger than the normal 1 MB limit
    resp = post_form(client, "/seller/products/new", photo=raw, filename="shirt.png")
    assert resp.status_code == 302, resp.get_data(as_text=True)[:400]
    p = newest(services)
    assert p["name"] == "Linen Shirt" and p["image_url"].startswith("/media/products/" + p["product_id"])
    photo = client.get(p["image_url"])
    assert photo.status_code == 200 and photo.mimetype == "image/jpeg" and photo.data[:3] == b"\xff\xd8\xff"
    html = client.get(f"/product/{p['product_id']}").get_data(as_text=True)
    assert f'src="{p["image_url"]}"' in html and 'class="photo photo--xl"' in html
    assert f'src="{p["image_url"]}"' in client.get("/seller/products").get_data(as_text=True)


def test_invalid_photo_stops_the_form_and_creates_nothing(client, services):
    login(client, "carlos@urbanthread.mx")
    before = len(services.store.products)
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'
    resp = post_form(client, "/seller/products/new", photo=svg, filename="photo.jpg")   # lying extension
    assert resp.status_code == 400 and "JPG, PNG or WebP" in resp.get_data(as_text=True)
    assert len(services.store.products) == before


def test_seller_replaces_and_removes_the_photo(client, services):
    login(client, "carlos@urbanthread.mx")
    post_form(client, "/seller/products/new", photo=image_bytes())
    pid = newest(services)["product_id"]
    old = services.store.products[pid]["image_url"]
    edit = f"/seller/products/{pid}/edit"
    assert post_form(client, edit, photo=image_bytes("PNG")).status_code == 302
    new = services.store.products[pid]["image_url"]
    assert new != old and client.get(old).status_code == 404 and client.get(new).status_code == 200
    assert post_form(client, edit, remove_photo="1").status_code == 302
    assert "image_url" not in services.store.products[pid] and client.get(new).status_code == 404
    assert "photo photo--xl" not in client.get(f"/product/{pid}").get_data(as_text=True)


def test_saving_without_a_new_file_keeps_the_photo(client, services):
    login(client, "carlos@urbanthread.mx")
    post_form(client, "/seller/products/new", photo=image_bytes())
    pid = newest(services)["product_id"]
    url = services.store.products[pid]["image_url"]
    assert post_form(client, f"/seller/products/{pid}/edit", price="499.00").status_code == 302
    assert services.store.products[pid]["image_url"] == url and services.store.products[pid]["price"] == 499


def test_big_bodies_are_still_rejected_everywhere_else(client, services):
    big = "x" * (2 * 1024 * 1024)
    tok = token(client)
    assert client.post("/seller/products/new", data={"csrf_token": tok, "name": big}).status_code == 413   # anonymous
    login(client, "ana@example.com")
    pid = some_product(services)["product_id"]
    resp = client.post("/cart/add", data={"csrf_token": token(client, "/cart"), "product_id": pid, "comment": big})
    assert resp.status_code == 413


def test_a_seller_cannot_set_the_photo_of_another_sellers_product(services):
    other = some_product(services, seller_id="SEL-0005")["product_id"]
    photo = prepare_photo(image_bytes())
    with pytest.raises(ValidationError):
        services.seller.set_photo(CARLOS_SELLER, other, photo)
    with pytest.raises(ValidationError):
        services.seller.remove_photo(CARLOS_SELLER, other)
    assert "image_url" not in services.store.products[other]


def test_media_route_only_serves_stored_photos(client):
    assert client.get("/media/products/PRD-000001/" + "a" * 32 + ".jpg").status_code == 404
    assert client.get("/media/products/PRD-000001/..%2F..%2F.env").status_code == 404


def test_csp_allows_only_the_configured_bucket(services):
    origin = "https://marketplace-photos.s3.us-east-1.amazonaws.com"
    app = create_app(services, config={"TESTING": True, "SECRET_KEY": "t", "PRODUCT_IMAGES_URL": origin + "/"})
    csp = app.test_client().get("/").headers["Content-Security-Policy"]
    assert f"img-src 'self' data: {origin};" in csp
    for bad in ("http://marketplace-photos.s3.us-east-1.amazonaws.com", "https://evil.example.com", "*"):
        app = create_app(services, config={"TESTING": True, "SECRET_KEY": "t", "PRODUCT_IMAGES_URL": bad})
        assert "img-src 'self' data:;" in app.test_client().get("/").headers["Content-Security-Policy"]


# ================= Flask app with PRODUCTS_BACKEND=aws (end to end) =================
def aws_services(table, images):
    service = ProductServiceImplementation(table, images=images)
    service._handler = lambda_handler
    client = ProductApiClient(base_url="https://api.example.test", api_key=KEY, http=LambdaHttp(service))
    upload(client, PAYLOADS, batch=100, out=lambda *a, **k: None)
    return build_services(product_count=1200, products_backend="aws", product_client=client)


def test_photo_flows_from_flask_to_s3_and_dynamodb(table):
    s3 = FakeS3Client()
    services = aws_services(table, S3ImageStore(BUCKET, "us-east-1", client=s3))
    pid = some_product(services, seller_id=CARLOS_SELLER)["product_id"]
    url = services.seller.set_photo(CARLOS_SELLER, pid, prepare_photo(image_bytes()))
    assert URL_RE.fullmatch(url) and stored(table, pid)["image_url"] == url
    assert services.store.products[pid]["image_url"] == url                 # cache updated after the write
    assert services.main.product_page(pid).product.image_url == url
    assert len(s3.objects) == 1

    product = services.seller.product(CARLOS_SELLER, pid)
    form = {"name": product.name, "description": product.description, "category_id": str(product.category_id),
            "price": "321.00", "stock": str(product.stock), "threshold_limit": str(product.threshold_limit),
            "sizes": ", ".join(product.sizes), "colors": ", ".join(product.colors), "visible": "1"}

    class Form(dict):
        def getlist(self, key):
            return []
    services.seller.update(CARLOS_SELLER, pid, Form(form))                  # editing keeps the photo
    assert stored(table, pid)["image_url"] == url

    services.seller.remove_photo(CARLOS_SELLER, pid)
    assert "image_url" not in stored(table, pid) and s3.objects == {}
    assert "image_url" not in services.store.products[pid]


def test_flask_explains_when_the_lambda_has_no_bucket(table):
    services = aws_services(table, images=None)
    pid = some_product(services, seller_id=CARLOS_SELLER)["product_id"]
    with pytest.raises(ImagesDisabled, match="MARKETPLACE_BUCKET"):
        services.seller.set_photo(CARLOS_SELLER, pid, prepare_photo(image_bytes()))
    assert "image_url" not in services.store.products[pid]
