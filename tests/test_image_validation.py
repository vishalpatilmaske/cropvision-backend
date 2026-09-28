import io

import pytest
from werkzeug.datastructures import FileStorage

from app.utils.image_validation import ImageValidationError, validate_and_load_image

ALLOWED_MIME = {"image/jpeg", "image/png", "image/webp"}
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}


def test_rejects_no_file():
    with pytest.raises(ImageValidationError) as exc:
        validate_and_load_image(None, 1024 * 1024, ALLOWED_MIME, ALLOWED_EXT)
    assert exc.value.code == "NO_FILE"


def test_rejects_empty_file():
    fs = FileStorage(stream=io.BytesIO(b""), filename="leaf.jpg", content_type="image/jpeg")
    with pytest.raises(ImageValidationError) as exc:
        validate_and_load_image(fs, 1024 * 1024, ALLOWED_MIME, ALLOWED_EXT)
    assert exc.value.code == "EMPTY_FILE"


def test_rejects_unsupported_extension():
    fs = FileStorage(stream=io.BytesIO(b"not-empty"), filename="leaf.gif", content_type="image/gif")
    with pytest.raises(ImageValidationError) as exc:
        validate_and_load_image(fs, 1024 * 1024, ALLOWED_MIME, ALLOWED_EXT)
    assert exc.value.code == "UNSUPPORTED_FORMAT"


def test_rejects_oversized_file():
    fs = FileStorage(stream=io.BytesIO(b"x" * 2048), filename="leaf.jpg", content_type="image/jpeg")
    with pytest.raises(ImageValidationError) as exc:
        validate_and_load_image(fs, 1024, ALLOWED_MIME, ALLOWED_EXT)
    assert exc.value.code == "FILE_TOO_LARGE"


def test_rejects_corrupt_image():
    fs = FileStorage(stream=io.BytesIO(b"definitely not an image"), filename="leaf.jpg", content_type="image/jpeg")
    with pytest.raises(ImageValidationError) as exc:
        validate_and_load_image(fs, 1024 * 1024, ALLOWED_MIME, ALLOWED_EXT)
    assert exc.value.code == "CORRUPT_IMAGE"


def test_accepts_valid_image(sample_image_bytes):
    fs = FileStorage(stream=io.BytesIO(sample_image_bytes), filename="leaf.jpg", content_type="image/jpeg")
    raw_bytes, mime_type = validate_and_load_image(fs, 1024 * 1024, ALLOWED_MIME, ALLOWED_EXT)
    assert raw_bytes == sample_image_bytes
    assert mime_type == "image/jpeg"
