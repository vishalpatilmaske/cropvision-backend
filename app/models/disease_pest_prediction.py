from datetime import datetime, timezone
from typing import Any, Dict, Optional

from app.extensions import mongo
from app.utils.mongo_helpers import contains_regex, iso, to_object_id


class DiseasePestPrediction:
    """Stores the outcome of one GPT-vision-based crop image analysis.

    Analysis is performed by a hosted vision-capable LLM (see
    app/services/ai/disease_pest_service.py) rather than a locally trained
    CNN, so this collection has no model-weights or training-related fields.
    """

    def __init__(self, doc: Dict[str, Any]):
        self._doc = doc

    @staticmethod
    def _col():
        return mongo.db.disease_pest_predictions

    @classmethod
    def create(
        cls, *, user_id, farm_id=None, crop_id=None, crop_name=None,
        analysis_type, condition_name=None, confidence=None, severity=None, assessment_level=None,
        symptoms=None, possible_causes=None, recommendations=None, additional_observations=None,
        needs_expert_confirmation=False, raw_response_reference=None, report=None,
        growth_stage=None, weather=None,
    ) -> "DiseasePestPrediction":
        doc = {
            "user_id": to_object_id(user_id),
            "farm_id": to_object_id(farm_id),
            "crop_id": to_object_id(crop_id),
            "crop_name": crop_name,
            "analysis_type": analysis_type,
            "condition_name": condition_name,
            "confidence": confidence,
            "severity": severity,
            "assessment_level": assessment_level,
            "symptoms": symptoms or [],
            "possible_causes": possible_causes or [],
            "recommendations": recommendations or {},
            "additional_observations": additional_observations or [],
            "needs_expert_confirmation": bool(needs_expert_confirmation),
            "raw_response_reference": raw_response_reference,
            "report": report or {},
            "growth_stage": growth_stage,
            "weather": weather,
            "created_at": datetime.now(timezone.utc),
        }
        result = cls._col().insert_one(doc)
        doc["_id"] = result.inserted_id
        return cls(doc)

    @classmethod
    def find_one_for_user(cls, prediction_id, user_id) -> Optional["DiseasePestPrediction"]:
        p_oid, u_oid = to_object_id(prediction_id), to_object_id(user_id)
        if not p_oid or not u_oid:
            return None
        doc = cls._col().find_one({"_id": p_oid, "user_id": u_oid})
        return cls(doc) if doc else None

    @classmethod
    def find_paginated(cls, *, user_id, page, per_page, crop_name=None, analysis_type=None):
        oid = to_object_id(user_id)
        query: Dict[str, Any] = {"user_id": oid}
        if crop_name:
            query["crop_name"] = contains_regex(crop_name)
        if analysis_type:
            query["analysis_type"] = analysis_type

        total = cls._col().count_documents(query)
        cursor = cls._col().find(query).sort("created_at", -1).skip((page - 1) * per_page).limit(per_page)
        items = [cls(doc) for doc in cursor]
        return items, total

    @classmethod
    def count(cls) -> int:
        return cls._col().count_documents({})

    @property
    def id(self) -> str:
        return str(self._doc["_id"])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": str(self._doc["_id"]),
            "user_id": str(self._doc["user_id"]) if self._doc.get("user_id") else None,
            "farm_id": str(self._doc["farm_id"]) if self._doc.get("farm_id") else None,
            "crop_id": str(self._doc["crop_id"]) if self._doc.get("crop_id") else None,
            "crop": {
                "name": self._doc.get("crop_name"),
                "growth_stage": self._doc.get("growth_stage"),
            },
            "analysis": {
                "type": self._doc.get("analysis_type"),
                "name": self._doc.get("condition_name"),
                "confidence": self._doc.get("confidence"),
                "severity": self._doc.get("severity"),
                "assessment_level": self._doc.get("assessment_level"),
                "symptoms": self._doc.get("symptoms") or [],
                "possible_causes": self._doc.get("possible_causes") or [],
            },
            "recommendations": self._doc.get("recommendations") or {},
            "additional_observations": self._doc.get("additional_observations") or [],
            "needs_expert_confirmation": self._doc.get("needs_expert_confirmation", False),
            "report": self._doc.get("report") or {},
            "weather": self._doc.get("weather"),
            "created_at": iso(self._doc.get("created_at")),
        }
