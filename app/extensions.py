"""Shared Flask extension instances, created without an app so they can be
initialized later via `init_app` from the application factory."""
from flask_jwt_extended import JWTManager
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from pymongo import MongoClient


class Mongo:
    """Thin wrapper so `mongo.db` is available app-wide once initialized."""

    def __init__(self):
        self.client: MongoClient | None = None
        self.db = None

    def init_app(self, app) -> None:
        # Fail fast (10 s, not 30 s) if the database is unreachable, e.g. an
        # Atlas IP allow-list blocking the host.
        self.client = MongoClient(app.config["MONGO_URI"], serverSelectionTimeoutMS=10000)
        # Atlas connection strings often omit the database name.
        self.db = self.client.get_default_database(default="cropvision")


mongo = Mongo()
jwt = JWTManager()
cors = CORS()
limiter = Limiter(key_func=get_remote_address, default_limits=[])
