"""Read-only aggregations for the admin panel: dashboard numbers, per-user
activity, and the all-farmers health-check list. Routes stay thin; every
query here runs across all users, so only admin routes may call it."""
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.models.crop import Crop
from app.models.disease_pest_prediction import DiseasePestPrediction
from app.models.farm import Farm
from app.models.newsletter import NewsletterSubscriber
from app.models.recommendation import (
    CropRecommendation,
    FertilizerRecommendation,
    IrrigationRecommendation,
    YieldPrediction,
)
from app.models.user import User
from app.utils.mongo_helpers import contains_regex, to_object_id

TREND_DAYS = 14
# The AI writes "unknown" when it can't tell -- not a real crop or condition.
_PLACEHOLDER = re.compile(r"^\s*unknown\s*$", re.IGNORECASE)

# Everything a farmer can create, keyed by the name the API reports it under.
_ACTIVITY_MODELS = {
    "health_checks": DiseasePestPrediction,
    "crop_plans": CropRecommendation,
    "fertilizer_plans": FertilizerRecommendation,
    "irrigation_plans": IrrigationRecommendation,
    "yield_estimates": YieldPrediction,
    "farms": Farm,
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _daily_counts(collection, since: datetime, days: int) -> List[Dict[str, Any]]:
    """[{date: "YYYY-MM-DD", count}] for each of the last `days` days (UTC), zeros included."""
    rows = collection.aggregate([
        {"$match": {"created_at": {"$gte": since}}},
        {"$group": {"_id": {"$dateToString": {"format": "%Y-%m-%d", "date": "$created_at"}}, "count": {"$sum": 1}}},
    ])
    counts = {row["_id"]: row["count"] for row in rows}
    dates = [(since + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]
    return [{"date": d, "count": counts.get(d, 0)} for d in dates]


def _top(collection, field: str, match: Dict[str, Any], limit: int = 5) -> List[Dict[str, Any]]:
    """Most common values of a text field, case-insensitive ("Tomato" == "tomato")."""
    rows = collection.aggregate([
        {"$match": {**match, field: {"$nin": [None, ""], "$not": _PLACEHOLDER}}},
        {"$group": {"_id": {"$toLower": f"${field}"}, "count": {"$sum": 1}, "label": {"$first": f"${field}"}}},
        {"$sort": {"count": -1, "_id": 1}},
        {"$limit": limit},
    ])
    return [{"name": row["label"], "count": row["count"]} for row in rows]


def dashboard_stats() -> Dict[str, Any]:
    now = _utc_now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    trend_start = today - timedelta(days=TREND_DAYS - 1)
    week_ago = now - timedelta(days=7)

    users = User._col()
    checks = DiseasePestPrediction._col()

    by_type = {row["_id"]: row["count"] for row in checks.aggregate([
        {"$group": {"_id": "$analysis_type", "count": {"$sum": 1}}},
    ]) if row["_id"]}
    avg_score = next(checks.aggregate([
        {"$match": {"report.health_score": {"$type": "number"}}},
        {"$group": {"_id": None, "avg": {"$avg": "$report.health_score"}}},
    ]), None)

    return {
        "totals": {
            "users": users.count_documents({}),
            **{name: model._col().count_documents({}) for name, model in _ACTIVITY_MODELS.items()},
        },
        "last_7_days": {
            "new_users": users.count_documents({"created_at": {"$gte": week_ago}}),
            "health_checks": checks.count_documents({"created_at": {"$gte": week_ago}}),
        },
        "health_checks_by_type": by_type,
        "average_health_score": round(avg_score["avg"], 1) if avg_score else None,
        "needs_expert_review": checks.count_documents({"needs_expert_confirmation": True}),
        "newsletter_subscribers": NewsletterSubscriber.count_active(),
        "top_conditions": _top(checks, "condition_name", {"analysis_type": {"$ne": "healthy"}}),
        "top_crops": _top(checks, "crop_name", {}),
        "trend": {
            "days": TREND_DAYS,
            "signups": _daily_counts(users, trend_start, TREND_DAYS),
            "health_checks": _daily_counts(checks, trend_start, TREND_DAYS),
        },
    }


def health_check_counts(user_ids: List[str]) -> Dict[str, Dict[str, Any]]:
    """{user_id: {"health_checks": n, "last_active": iso}} for a page of users."""
    oids = [oid for oid in (to_object_id(u) for u in user_ids) if oid]
    if not oids:
        return {}
    rows = DiseasePestPrediction._col().aggregate([
        {"$match": {"user_id": {"$in": oids}}},
        {"$group": {"_id": "$user_id", "count": {"$sum": 1}, "last": {"$max": "$created_at"}}},
    ])
    return {
        str(row["_id"]): {
            "health_checks": row["count"],
            "last_active": row["last"].replace(tzinfo=timezone.utc).isoformat() if row["last"] else None,
        }
        for row in rows
    }


def user_activity(user_id: str) -> Dict[str, Any]:
    """Counts of everything the user created, plus their latest health checks."""
    oid = to_object_id(user_id)
    counts = {name: model._col().count_documents({"user_id": oid}) for name, model in _ACTIVITY_MODELS.items()}
    farm_ids = [f["_id"] for f in Farm._col().find({"user_id": oid}, {"_id": 1})]
    counts["crops"] = Crop._col().count_documents({"farm_id": {"$in": farm_ids}}) if farm_ids else 0
    recent, _ = DiseasePestPrediction.find_paginated(user_id=user_id, page=1, per_page=5)
    return {"counts": counts, "recent_health_checks": [summarize_check(r.to_dict()) for r in recent]}


def summarize_check(record: Dict[str, Any]) -> Dict[str, Any]:
    analysis = record["analysis"]
    return {
        "id": record["id"],
        "user_id": record["user_id"],
        "crop": record["crop"]["name"],
        "type": analysis["type"],
        "condition": analysis["name"],
        "severity": analysis["severity"],
        "confidence": analysis["confidence"],
        "health_score": (record.get("report") or {}).get("health_score"),
        "needs_expert_confirmation": record["needs_expert_confirmation"],
        "created_at": record["created_at"],
    }


def list_health_checks(
    *, page: int, per_page: int, analysis_type: Optional[str] = None,
    crop_name: Optional[str] = None, user_id: Optional[str] = None, needs_review: bool = False,
):
    """All farmers' health checks, newest first, each with its owner's name and email."""
    query: Dict[str, Any] = {}
    if analysis_type:
        query["analysis_type"] = analysis_type
    if crop_name:
        query["crop_name"] = contains_regex(crop_name)
    if user_id:
        query["user_id"] = to_object_id(user_id)
    if needs_review:
        query["needs_expert_confirmation"] = True

    col = DiseasePestPrediction._col()
    total = col.count_documents(query)
    docs = list(col.find(query).sort("created_at", -1).skip((page - 1) * per_page).limit(per_page))
    owners = _owners([d.get("user_id") for d in docs])
    items = []
    for doc in docs:
        summary = summarize_check(DiseasePestPrediction(doc).to_dict())
        summary["user"] = owners.get(summary["user_id"])
        items.append(summary)
    return items, total


def _owners(user_oids) -> Dict[str, Dict[str, str]]:
    ids = list({oid for oid in user_oids if oid})
    return {
        str(doc["_id"]): {"id": str(doc["_id"]), "name": doc.get("name"), "email": doc.get("email")}
        for doc in User._col().find({"_id": {"$in": ids}}, {"name": 1, "email": 1})
    }


def get_health_check(check_id: str) -> Optional[Dict[str, Any]]:
    oid = to_object_id(check_id)
    doc = DiseasePestPrediction._col().find_one({"_id": oid}) if oid else None
    if not doc:
        return None
    record = DiseasePestPrediction(doc).to_dict()
    record["user"] = _owners([doc.get("user_id")]).get(record["user_id"])
    return record


def delete_health_check(check_id: str) -> bool:
    oid = to_object_id(check_id)
    return bool(oid) and DiseasePestPrediction._col().delete_one({"_id": oid}).deleted_count > 0
