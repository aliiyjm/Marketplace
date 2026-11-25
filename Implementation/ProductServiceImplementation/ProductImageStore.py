"""Product photos in Amazon S3 (runs inside the Lambda).

Files are stored as  products/PRD-000123/<32 random hex>.jpg
- The name is random: nobody can guess or overwrite another product's photo,
  and a new photo never reuses an old (cached) URL.
- Content-Type is set from the real file type, never from the client.
- Only objects under products/ of THIS bucket are ever deleted.

The bucket allows public READ of products/* only (bucket policy); uploads and
deletes go exclusively through this Lambda with its role (LabRole).
"""
import re

from Interfaces.ProductServiceInterface.ProductDTO import IMAGE_KEY_RE
from Interfaces.ProductServiceInterface.ProductServiceInterface import ProductImageStoreInterface

BUCKET_RE = re.compile(r"[a-z0-9][a-z0-9-]{1,61}[a-z0-9]")     # no dots: keeps the HTTPS certificate valid
REGION_RE = re.compile(r"[a-z]{2}(?:-[a-z]+)+-\d")
CACHE_CONTROL = "public, max-age=31536000, immutable"          # the name changes with every new photo


class S3ImageStore(ProductImageStoreInterface):
    def __init__(self, bucket, region, client=None):
        if not BUCKET_RE.fullmatch(bucket or ""):
            raise ValueError("MARKETPLACE_BUCKET must be a bucket name with lowercase letters, numbers and dashes.")
        if not REGION_RE.fullmatch(region or ""):
            raise ValueError("The AWS region is invalid.")
        self.bucket = bucket
        self.base_url = f"https://{bucket}.s3.{region}.amazonaws.com/"
        self._client = client

    @property
    def client(self):
        if self._client is None:
            import boto3            # comes with the Lambda runtime
            self._client = boto3.client("s3")
        return self._client

    def put(self, key, data, content_type):
        if not IMAGE_KEY_RE.fullmatch(key):
            raise ValueError("Invalid photo key.")
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type,
                               CacheControl=CACHE_CONTROL)

    def delete(self, key):
        if IMAGE_KEY_RE.fullmatch(key or ""):
            self.client.delete_object(Bucket=self.bucket, Key=key)

    def url(self, key):
        return self.base_url + key

    def key_of(self, url):
        if url and url.startswith(self.base_url):
            key = url[len(self.base_url):]
            if IMAGE_KEY_RE.fullmatch(key):
                return key
        return None
