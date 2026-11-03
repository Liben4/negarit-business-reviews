"""Object-storage adapters. The Vercel filesystem is NEVER used for media in production."""
import hashlib
import secrets
import time

import requests
from flask import current_app


class UploadError(ValueError):
    pass


class Storage:
    def save(self, data: bytes, ext: str, content_type: str, name: str) -> tuple[str, str]:
        """Store bytes; return (public_url, storage_key)."""
        raise NotImplementedError

    def delete(self, key: str) -> None:
        raise NotImplementedError

    @staticmethod
    def new_key(ext: str, name: str) -> str:
        return f"{time.strftime('%Y/%m')}/{name}-{secrets.token_hex(8)}.{ext}"


class LocalStorage(Storage):
    """Development only."""

    def __init__(self, root):
        self.root = root

    def save(self, data, ext, content_type, name):
        import os
        key = self.new_key(ext, name)
        path = os.path.join(self.root, key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        return f"/media/{key}", key

    def delete(self, key):
        import os
        try:
            os.remove(os.path.join(self.root, key))
        except OSError:
            pass


class CloudinaryStorage(Storage):
    def __init__(self, cloud, key, secret):
        if not (cloud and key and secret):
            raise UploadError("Cloudinary is not configured.")
        self.cloud, self.api_key, self.secret = cloud, key, secret

    def _sign(self, params: dict) -> str:
        return hashlib.sha1(("&".join(f"{k}={v}" for k, v in sorted(params.items())) + self.secret).encode()).hexdigest()

    def save(self, data, ext, content_type, name):
        params = {"folder": "negarit", "timestamp": str(int(time.time()))}
        r = requests.post(f"https://api.cloudinary.com/v1_1/{self.cloud}/image/upload",
                          data={**params, "api_key": self.api_key, "signature": self._sign(params)},
                          files={"file": (f"{name}.{ext}", data, content_type)}, timeout=25)
        if r.status_code != 200:
            current_app.logger.error("Cloudinary upload failed: %s %s", r.status_code, r.text[:300])
            raise UploadError("Image storage failed. Try again.")
        j = r.json()
        return j["secure_url"], j["public_id"]

    def delete(self, key):
        params = {"public_id": key, "timestamp": str(int(time.time()))}
        try:
            requests.post(f"https://api.cloudinary.com/v1_1/{self.cloud}/image/destroy",
                          data={**params, "api_key": self.api_key, "signature": self._sign(params)}, timeout=15)
        except requests.RequestException:
            current_app.logger.warning("Cloudinary delete failed for %s", key)


class S3Storage(Storage):
    """AWS S3 or S3-compatible (Cloudflare R2, Backblaze B2, DigitalOcean Spaces)."""

    def __init__(self, cfg):
        try:
            import boto3
        except ImportError:  # pragma: no cover
            raise UploadError("boto3 is not installed (see requirements.txt).")
        if not (cfg["STORAGE_CLOUD_NAME"] and cfg["STORAGE_API_KEY"] and cfg["STORAGE_API_SECRET"]
                and cfg["STORAGE_PUBLIC_BASE_URL"]):
            raise UploadError("S3 storage is not fully configured.")
        self.bucket, self.base = cfg["STORAGE_CLOUD_NAME"], cfg["STORAGE_PUBLIC_BASE_URL"]
        self.client = boto3.client("s3", aws_access_key_id=cfg["STORAGE_API_KEY"],
                                   aws_secret_access_key=cfg["STORAGE_API_SECRET"],
                                   region_name=cfg["STORAGE_REGION"] or None,
                                   endpoint_url=cfg["STORAGE_ENDPOINT_URL"] or None)

    def save(self, data, ext, content_type, name):
        key = "negarit/" + self.new_key(ext, name)
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type,
                               CacheControl="public, max-age=31536000, immutable")
        return f"{self.base}/{key}", key

    def delete(self, key):
        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except Exception:  # noqa: BLE001
            current_app.logger.warning("S3 delete failed for %s", key)


def get_storage() -> Storage:
    """Resolve config on the request thread; the returned adapter is thread-safe."""
    import os
    c = current_app.config
    provider = (c["STORAGE_PROVIDER"] or "").lower()
    if provider == "local":
        if c["STRICT"]:
            raise UploadError("Local storage is disabled in production. Set STORAGE_PROVIDER.")
        return LocalStorage(os.path.join(current_app.instance_path, "uploads"))
    if provider == "cloudinary":
        return CloudinaryStorage(c["STORAGE_CLOUD_NAME"], c["STORAGE_API_KEY"], c["STORAGE_API_SECRET"])
    if provider == "s3":
        return S3Storage(c)
    raise UploadError("No media storage provider configured (STORAGE_PROVIDER).")
