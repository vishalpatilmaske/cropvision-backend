import io
import re

import pytest
from PIL import Image

from app import create_app
from app.config import TestConfig
from app.extensions import mongo as _mongo
from app.services import email_service


@pytest.fixture()
def app():
    application = create_app(TestConfig)
    with application.app_context():
        _mongo.client.drop_database(_mongo.db.name)
        yield application
        _mongo.client.drop_database(_mongo.db.name)


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def clear_outbox():
    email_service.outbox.clear()
    yield
    email_service.outbox.clear()


def last_code(email):
    """The 6-digit code from the most recent test email sent to `email`."""
    message = next(m for m in reversed(email_service.outbox) if m["to"] == email)
    return re.search(r"\b(\d{6})\b", message["text"]).group(1)


def sign_up(client, email="farmer@example.com", name="Test Farmer", phone=None):
    client.post("/api/auth/otp/request", json={"email": email, "purpose": "register", "name": name, "phone": phone})
    resp = client.post(
        "/api/auth/otp/verify", json={"email": email, "purpose": "register", "code": last_code(email)}
    )
    return resp.get_json()["data"]["access_token"]


@pytest.fixture()
def auth_headers(client):
    return {"Authorization": f"Bearer {sign_up(client)}"}


@pytest.fixture()
def admin_headers(client):
    resp = client.post(
        "/api/admin/login", json={"email": "admin@gmail.com", "password": "123456"}
    )
    token = resp.get_json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def sample_image_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (100, 100), color=(0, 128, 0)).save(buf, format="JPEG")
    buf.seek(0)
    return buf.read()
