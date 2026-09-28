from datetime import datetime, timezone
from typing import Any, Dict, List

from app.extensions import mongo
from app.utils.mongo_helpers import iso, to_object_id


class Crop:
    def __init__(self, doc: Dict[str, Any]):
        self._doc = doc

    @staticmethod
    def _col():
        return mongo.db.crops

    @classmethod
    def create(cls, *, farm_id, name, variety=None, growth_stage=None, planted_on=None) -> "Crop":
        doc = {
            "farm_id": to_object_id(farm_id),
            "name": name,
            "variety": variety,
            "growth_stage": growth_stage,
            "planted_on": planted_on,
            "created_at": datetime.now(timezone.utc),
        }
        result = cls._col().insert_one(doc)
        doc["_id"] = result.inserted_id
        return cls(doc)

    @classmethod
    def find_by_farm_ids(cls, farm_ids) -> List["Crop"]:
        oids = [oid for oid in (to_object_id(f) for f in farm_ids) if oid]
        if not oids:
            return []
        cursor = cls._col().find({"farm_id": {"$in": oids}}).sort("created_at", -1)
        return [cls(doc) for doc in cursor]

    @property
    def id(self) -> str:
        return str(self._doc["_id"])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self._doc["_id"]),
            "farm_id": str(self._doc["farm_id"]),
            "name": self._doc.get("name"),
            "variety": self._doc.get("variety"),
            "growth_stage": self._doc.get("growth_stage"),
            "planted_on": self._doc.get("planted_on"),
            "created_at": iso(self._doc.get("created_at")),
        }
