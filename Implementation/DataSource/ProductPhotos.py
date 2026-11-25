"""Product photos uploaded by sellers: validated and RE-ENCODED before storing.

Why re-encode instead of saving the file as it came:
- Metadata is dropped: phone photos carry EXIF with the GPS location of the
  seller's home, the camera serial number, etc.
- Anything hidden in the file (a script or HTML appended to the image, a
  "polyglot" file) disappears: only the decoded pixels are written again.
- Size is normalized (max 1200 px, JPEG): pages load fast and the upload to
  the Lambda stays far below its 6 MB payload limit.

Defenses against malicious files:
- The type is detected from the first bytes (never the name or Content-Type).
- Only JPEG, PNG and WebP. Never SVG (an SVG is XML and can run scripts).
- Pixel count is checked BEFORE decoding (decompression bombs: a tiny file
  that expands to gigabytes of memory).
- Any decoder error becomes a friendly ValidationError.
"""
import io
import warnings

from PIL import Image, ImageOps, UnidentifiedImageError

from Interfaces.ProductServiceInterface.ProductDTO import MAX_IMAGE_BYTES, MAX_UPLOAD_BYTES, image_type
from Interfaces.Validators import ValidationError

MAX_PIXELS = 40_000_000                   # 40 MP: any phone photo, but no bombs
MAX_SIDE = 1200                           # stored photo: longest side in px
OUTPUT_TYPE = "image/jpeg"
ALLOWED_FORMATS = {"JPEG", "PNG", "WEBP"}
_QUALITIES = (85, 75, 65)


def _open(raw):
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        try:
            img = Image.open(io.BytesIO(raw))
            if img.format not in ALLOWED_FORMATS:
                raise ValidationError("Upload a JPG, PNG or WebP photo.")
            width, height = img.size
            if width < 1 or height < 1 or width * height > MAX_PIXELS:
                raise ValidationError("The photo has too many pixels (max 40 megapixels).")
            img.load()                    # decode now: truncated/corrupt files fail here
            return img
        except ValidationError:
            raise
        except (UnidentifiedImageError, Image.DecompressionBombError, Image.DecompressionBombWarning,
                OSError, SyntaxError, ValueError, EOFError):
            raise ValidationError("The photo could not be read. Try another file.") from None


def _to_rgb(img):
    img = ImageOps.exif_transpose(img)    # keep the orientation the phone intended, then EXIF is gone
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        background = Image.new("RGB", rgba.size, (255, 255, 255))
        background.paste(rgba, mask=rgba.getchannel("A"))
        return background
    return img.convert("RGB")


def prepare_photo(raw):
    """Untrusted upload (bytes) -> (clean JPEG bytes, "image/jpeg").
    Raises ValidationError with a message that can be shown to the seller."""
    if not raw:
        raise ValidationError("The photo file is empty.")
    if len(raw) > MAX_UPLOAD_BYTES:
        raise ValidationError("The photo must be 5 MB or less.")
    if image_type(raw) is None:
        raise ValidationError("Upload a JPG, PNG or WebP photo.")
    img = _to_rgb(_open(raw))
    img.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
    for quality in _QUALITIES:
        out = io.BytesIO()
        # No exif=/icc_profile= arguments: the new file carries no metadata at all.
        img.save(out, "JPEG", quality=quality, optimize=True, progressive=True)
        data = out.getvalue()
        if len(data) <= MAX_IMAGE_BYTES:
            return data, OUTPUT_TYPE
    raise ValidationError("The photo is too detailed to store. Try a smaller one.")
