from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.extensions import mongo
from app.utils.mongo_helpers import iso, to_object_id


class Farm:
    def __init__(self, doc: Dict[str, Any]):
        self._doc = doc

    @staticmethod
    def _col():
        return mongo.db.farms

    @classmethod
    def find_by_user(cls, user_id) -> List["Farm"]:
        oid = to_object_id(user_id)
        if not oid:
            return []
        cursor = cls._col().find({"user_id": oid}).sort("created_at", -1)
        return [cls(doc) for doc in cursor]

    @classmethod
    def find_one_for_user(cls, farm_id, user_id) -> Optional["Farm"]:
        f_oid, u_oid = to_object_id(farm_id), to_object_id(user_id)
        if not f_oid or not u_oid:
            return None
        doc = cls._col().find_one({"_id": f_oid, "user_id": u_oid})
        return cls(doc) if doc else None

    @classmethod
    def create(cls, *, user_id, name, location_text=None, latitude=None, longitude=None,
               area_acres=None, soil_type=None) -> "Farm":
        doc = {
            "user_id": to_object_id(user_id),
            "name": name,
            "location_text": location_text,
            "latitude": latitude,
            "longitude": longitude,
            "area_acres": area_acres,
            "soil_type": soil_type,
            "created_at": datetime.now(timezone.utc),
        }
        result = cls._col().insert_one(doc)
        doc["_id"] = result.inserted_id
        return cls(doc)

    @property
    def id(self) -> str:
        return str(self._doc["_id"])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self._doc["_id"]),
            "user_id": str(self._doc["user_id"]),
            "name": self._doc.get("name"),
            "location_text": self._doc.get("location_text"),
            "latitude": self._doc.get("latitude"),
            "longitude": self._doc.get("longitude"),
            "area_acres": self._doc.get("area_acres"),
            "soil_type": self._doc.get("soil_type"),
            "created_at": iso(self._doc.get("created_at")),
        }
