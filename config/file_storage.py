"""Stores files on Cloudinary. Render's disk is wiped on every deploy, so nothing is kept locally.

The Cloudinary SDK reads its account from the CLOUDINARY_URL environment variable
(cloudinary://<api_key>:<api_secret>@<cloud_name>). Never log that value.
"""

import logging
import os

logger = logging.getLogger(__name__)


class StorageUnavailable(Exception):
    """Storage is not set up, or Cloudinary could not take or remove the file."""


def is_configured() -> bool:
    return bool(os.environ.get("CLOUDINARY_URL", "").strip())


def store_private(data: bytes, public_id: str) -> dict:
    """Uploads the exact bytes as a private raw file. Returns {"public_id", "bytes"}.

    `type="authenticated"` means the file cannot be opened with a plain URL; a signed URL
    is needed, so a leaked link never exposes payroll data.
    """
    if not is_configured():
        raise StorageUnavailable("CLOUDINARY_URL is not set")
    try:
        # Imported here: the SDK parses CLOUDINARY_URL on import and raises ValueError when it is malformed.
        import cloudinary.uploader

        result = cloudinary.uploader.upload(
            data, public_id=public_id, resource_type="raw", type="authenticated", overwrite=False
        )
    except Exception as exc:
        logger.warning("Cloudinary upload failed for %s: %s", public_id, type(exc).__name__)
        raise StorageUnavailable("Cloudinary upload failed") from exc
    return {"public_id": result["public_id"], "bytes": result.get("bytes", len(data))}


def delete(public_id: str) -> None:
    """Removes a stored file. Failures are logged, not raised: this is cleanup."""
    if not public_id or not is_configured():
        return
    try:
        import cloudinary.uploader

        cloudinary.uploader.destroy(public_id, resource_type="raw", type="authenticated", invalidate=True)
    except Exception as exc:
        logger.warning("Cloudinary delete failed for %s: %s", public_id, type(exc).__name__)
