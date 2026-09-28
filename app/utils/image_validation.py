"""Image upload validation: MIME type, size, integrity and filename checks,
plus a resize/compress step for cost control before sending to the vision API.
Images are only held in memory -- nothing is written to disk.
"""
import io
import logging
import os
from typing import Optional, Tuple

from PIL import Image, UnidentifiedImageError
from werkzeug.datastructures import FileStorage
from werkzeug.utils import secure_filename

logger = logging.getLogger("cropvision.utils.image_validation")

_EXT_TO_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


class ImageValidationError(Exception):
    def __init__(self, message: str, code: str = "INVALID_IMAGE"):
        super().__init__(message)
        self.code = code


def validate_and_load_image(
    file_storage: Optional[FileStorage],
    max_size_bytes: int,
    allowed_mime_types: set,
    allowed_extensions: set,
) -> Tuple[bytes, str]:
    """Validate an uploaded file and return (image_bytes, mime_type).

    Raises ImageValidationError with a user-facing message and a stable
    error code for any validation failure.
    """
    if file_storage is None or not file_storage.filename:
        raise ImageValidationError("No image file was uploaded.", code="NO_FILE")

    safe_name = secure_filename(file_storage.filename)
    if not safe_name:
        raise ImageValidationError("Uploaded filename is invalid.", code="INVALID_FILENAME")

    _, ext = os.path.splitext(safe_name.lower())
    if ext not in allowed_extensions:
        raise ImageValidationError(
            f"Unsupported file extension '{ext}'. Supported: {sorted(allowed_extensions)}",
            code="UNSUPPORTED_FORMAT",
        )

    raw_bytes = file_storage.read()
    if not raw_bytes:
        raise ImageValidationError("Uploaded image file is empty.", code="EMPTY_FILE")

    if len(raw_bytes) > max_size_bytes:
        raise ImageValidationError(
            f"Image is too large ({len(raw_bytes) / (1024*1024):.1f} MB). "
            f"Maximum allowed is {max_size_bytes / (1024*1024):.1f} MB.",
            code="FILE_TOO_LARGE",
        )

    mime_type = file_storage.content_type or _EXT_TO_MIME.get(ext, "")
    if mime_type not in allowed_mime_types:
        raise ImageValidationError(
            f"Unsupported image type '{mime_type}'. Supported: {sorted(allowed_mime_types)}",
            code="UNSUPPORTED_FORMAT",
        )

    try:
        image = Image.open(io.BytesIO(raw_bytes))
        image.verify()
        image = Image.open(io.BytesIO(raw_bytes))
        image.load()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise ImageValidationError(
            "The uploaded file is not a valid or readable image.", code="CORRUPT_IMAGE"
        ) from exc

    if image.mode not in ("RGB", "L"):
        image = image.convert("RGB")

    return raw_bytes, mime_type


def resize_for_analysis(image_bytes: bytes, max_dimension_px: int) -> bytes:
    """Downscale + re-encode as JPEG to control payload size / API cost,
    while keeping enough detail for symptom analysis."""
    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
        if image.mode != "RGB":
            image = image.convert("RGB")

        width, height = image.size
        if max(width, height) > max_dimension_px:
            scale = max_dimension_px / max(width, height)
            image = image.resize((int(width * scale), int(height * scale)), Image.LANCZOS)

        buffer = io.BytesIO()
        image.save(buffer, format="JPEG", quality=85, optimize=True)
        return buffer.getvalue()
    except Exception:
        logger.exception("Failed to resize image for analysis; using original bytes.")
        return image_bytes
