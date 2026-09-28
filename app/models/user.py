from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.extensions import mongo
from app.utils.contact_checks import phone_key
from app.utils.mongo_helpers import contains_regex, iso, to_object_id


class User:
    """Thin wrapper around a document in the `users` collection."""

    def __init__(self, doc: Dict[str, Any]):
        self._doc = doc

    @staticmethod
    def _col():
        return mongo.db.users

    @classmethod
    def ensure_indexes(cls) -> None:
        """One account per email and per phone number. Older records get their
        phone_key filled in first so the unique index can be built."""
        col = cls._col()
        for doc in col.find({"phone": {"$nin": [None, ""]}, "phone_key": {"$exists": False}}, {"phone": 1}):
            col.update_one({"_id": doc["_id"]}, {"$set": {"phone_key": phone_key(doc["phone"])}})
        col.create_index("email", unique=True)
        col.create_index(
            "phone_key", unique=True, partialFilterExpression={"phone_key": {"$type": "string"}}
        )

    # --- lookups -----------------------------------------------------
    @classmethod
    def find_by_email(cls, email: str) -> Optional["User"]:
        doc = cls._col().find_one({"email": email})
        return cls(doc) if doc else None

    @classmethod
    def find_by_phone(cls, phone: Optional[str]) -> Optional["User"]:
        key = phone_key(phone)
        doc = cls._col().find_one({"phone_key": key}) if key else None
        return cls(doc) if doc else None

    @classmethod
    def find_by_id(cls, user_id) -> Optional["User"]:
        oid = to_object_id(user_id)
        if not oid:
            return None
        doc = cls._col().find_one({"_id": oid})
        return cls(doc) if doc else None

    SORTS = {
        "newest": [("created_at", -1)],
        "oldest": [("created_at", 1)],
        "name": [("name", 1), ("created_at", -1)],
    }

    @classmethod
    def find_all(cls, page: int = 1, per_page: int = 20, search: Optional[str] = None, sort: str = "newest"):
        query: Dict[str, Any] = {}
        if search:
            query = {"$or": [
                {"name": contains_regex(search)},
                {"email": contains_regex(search)},
                {"phone": contains_regex(search)},
            ]}
        cursor = cls._col().find(query).sort(cls.SORTS.get(sort, cls.SORTS["newest"]))
        total = cls._col().count_documents(query)
        items = [cls(doc) for doc in cursor.skip((page - 1) * per_page).limit(per_page)]
        return items, total

    @classmethod
    def count(cls) -> int:
        return cls._col().count_documents({})

    # --- mutations -----------------------------------------------------
    @classmethod
    def create(cls, name: str, email: str, phone: Optional[str] = None) -> "User":
        """Farmers sign in with an emailed code, so accounts have no password."""
        doc = {
            "name": name,
            "email": email,
            "phone": phone,
            "created_at": datetime.now(timezone.utc),
        }
        if phone_key(phone):
            doc["phone_key"] = phone_key(phone)
        result = cls._col().insert_one(doc)
        doc["_id"] = result.inserted_id
        return cls(doc)

    def update(self, fields: Dict[str, Any]) -> None:
        allowed = {k: v for k, v in fields.items() if k in {"name", "email", "phone"}}
        if not allowed:
            return
        update = {"$set": allowed}
        if "phone" in allowed:
            key = phone_key(allowed["phone"])
            if key:
                allowed["phone_key"] = key
            else:
                update["$unset"] = {"phone_key": ""}
        self._col().update_one({"_id": self._doc["_id"]}, update)
        self._doc.update(allowed)

    @classmethod
    def delete_by_id(cls, user_id) -> bool:
        """Deletes the user and cascades to everything owned by them
        (Mongo has no foreign-key ON DELETE CASCADE, so this is explicit)."""
        oid = to_object_id(user_id)
        if not oid:
            return False

        from app.models.crop import Crop
        from app.models.disease_pest_prediction import DiseasePestPrediction
        from app.models.farm import Farm
        from app.models.recommendation import (
            CropRecommendation,
            FertilizerRecommendation,
            IrrigationRecommendation,
            YieldPrediction,
        )

        farm_ids = [f["_id"] for f in Farm._col().find({"user_id": oid}, {"_id": 1})]
        if farm_ids:
            Crop._col().delete_many({"farm_id": {"$in": farm_ids}})
        Farm._col().delete_many({"user_id": oid})
        DiseasePestPrediction._col().delete_many({"user_id": oid})
        CropRecommendation._col().delete_many({"user_id": oid})
        FertilizerRecommendation._col().delete_many({"user_id": oid})
        IrrigationRecommendation._col().delete_many({"user_id": oid})
        YieldPrediction._col().delete_many({"user_id": oid})

        result = cls._col().delete_one({"_id": oid})
        return result.deleted_count > 0

    # --- instance helpers -----------------------------------------------------
    @property
    def id(self) -> str:
        return str(self._doc["_id"])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self._doc["_id"]),
            "name": self._doc.get("name"),
            "email": self._doc.get("email"),
            "phone": self._doc.get("phone"),
            "created_at": iso(self._doc.get("created_at")),
        }
