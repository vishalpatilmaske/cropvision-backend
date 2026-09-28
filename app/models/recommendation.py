from datetime import datetime, timezone
from typing import Any, Dict

from app.extensions import mongo
from app.utils.mongo_helpers import iso, to_object_id


def _find_paginated(cls, user_id, page, per_page):
    query = {"user_id": to_object_id(user_id)}
    total = cls._col().count_documents(query)
    cursor = cls._col().find(query).sort("created_at", -1).skip((page - 1) * per_page).limit(per_page)
    return [cls(doc) for doc in cursor], total


class CropRecommendation:
    def __init__(self, doc: Dict[str, Any]):
        self._doc = doc

    @staticmethod
    def _col():
        return mongo.db.crop_recommendations

    @classmethod
    def create(cls, *, user_id, farm_id=None, inputs, recommended_crops) -> "CropRecommendation":
        doc = {
            "user_id": to_object_id(user_id),
            "farm_id": to_object_id(farm_id),
            "inputs": inputs or {},
            "recommended_crops": recommended_crops or [],
            "created_at": datetime.now(timezone.utc),
        }
        result = cls._col().insert_one(doc)
        doc["_id"] = result.inserted_id
        return cls(doc)

    @classmethod
    def find_paginated(cls, *, user_id, page, per_page):
        return _find_paginated(cls, user_id, page, per_page)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self._doc["_id"]),
            "user_id": str(self._doc["user_id"]) if self._doc.get("user_id") else None,
            "farm_id": str(self._doc["farm_id"]) if self._doc.get("farm_id") else None,
            "inputs": self._doc.get("inputs") or {},
            "recommended_crops": self._doc.get("recommended_crops") or [],
            "created_at": iso(self._doc.get("created_at")),
        }


class FertilizerRecommendation:
    def __init__(self, doc: Dict[str, Any]):
        self._doc = doc

    @classmethod
    def find_paginated(cls, *, user_id, page, per_page):
        return _find_paginated(cls, user_id, page, per_page)

    @staticmethod
    def _col():
        return mongo.db.fertilizer_recommendations

    @classmethod
    def create(cls, *, user_id, farm_id=None, crop_id=None, inputs, recommendation) -> "FertilizerRecommendation":
        doc = {
            "user_id": to_object_id(user_id),
            "farm_id": to_object_id(farm_id),
            "crop_id": to_object_id(crop_id),
            "inputs": inputs or {},
            "recommendation": recommendation or {},
            "created_at": datetime.now(timezone.utc),
        }
        result = cls._col().insert_one(doc)
        doc["_id"] = result.inserted_id
        return cls(doc)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self._doc["_id"]),
            "user_id": str(self._doc["user_id"]) if self._doc.get("user_id") else None,
            "farm_id": str(self._doc["farm_id"]) if self._doc.get("farm_id") else None,
            "crop_id": str(self._doc["crop_id"]) if self._doc.get("crop_id") else None,
            "inputs": self._doc.get("inputs") or {},
            "recommendation": self._doc.get("recommendation") or {},
            "created_at": iso(self._doc.get("created_at")),
        }


class IrrigationRecommendation:
    def __init__(self, doc: Dict[str, Any]):
        self._doc = doc

    @classmethod
    def find_paginated(cls, *, user_id, page, per_page):
        return _find_paginated(cls, user_id, page, per_page)

    @staticmethod
    def _col():
        return mongo.db.irrigation_recommendations

    @classmethod
    def create(cls, *, user_id, farm_id=None, crop_id=None, weather_snapshot, soil_snapshot,
               advisory, plan=None) -> "IrrigationRecommendation":
        doc = {
            "user_id": to_object_id(user_id),
            "farm_id": to_object_id(farm_id),
            "crop_id": to_object_id(crop_id),
            "weather_snapshot": weather_snapshot or {},
            "soil_snapshot": soil_snapshot or {},
            "advisory": advisory or {},
            "plan": plan,
            "created_at": datetime.now(timezone.utc),
        }
        result = cls._col().insert_one(doc)
        doc["_id"] = result.inserted_id
        return cls(doc)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self._doc["_id"]),
            "user_id": str(self._doc["user_id"]) if self._doc.get("user_id") else None,
            "farm_id": str(self._doc["farm_id"]) if self._doc.get("farm_id") else None,
            "crop_id": str(self._doc["crop_id"]) if self._doc.get("crop_id") else None,
            "weather_snapshot": self._doc.get("weather_snapshot") or {},
            "soil_snapshot": self._doc.get("soil_snapshot") or {},
            "advisory": self._doc.get("advisory") or {},
            "plan": self._doc.get("plan"),
            "created_at": iso(self._doc.get("created_at")),
        }


class YieldPrediction:
    def __init__(self, doc: Dict[str, Any]):
        self._doc = doc

    @classmethod
    def find_paginated(cls, *, user_id, page, per_page):
        return _find_paginated(cls, user_id, page, per_page)

    @staticmethod
    def _col():
        return mongo.db.yield_predictions

    @classmethod
    def create(cls, *, user_id, farm_id=None, crop_id=None, inputs, estimated_yield) -> "YieldPrediction":
        doc = {
            "user_id": to_object_id(user_id),
            "farm_id": to_object_id(farm_id),
            "crop_id": to_object_id(crop_id),
            "inputs": inputs or {},
            "estimated_yield": estimated_yield or {},
            "created_at": datetime.now(timezone.utc),
        }
        result = cls._col().insert_one(doc)
        doc["_id"] = result.inserted_id
        return cls(doc)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self._doc["_id"]),
            "user_id": str(self._doc["user_id"]) if self._doc.get("user_id") else None,
            "farm_id": str(self._doc["farm_id"]) if self._doc.get("farm_id") else None,
            "crop_id": str(self._doc["crop_id"]) if self._doc.get("crop_id") else None,
            "inputs": self._doc.get("inputs") or {},
            "estimated_yield": self._doc.get("estimated_yield") or {},
            "created_at": iso(self._doc.get("created_at")),
        }
