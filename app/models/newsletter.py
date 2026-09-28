from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from pymongo.errors import DuplicateKeyError

from app.extensions import mongo
from app.utils.mongo_helpers import iso


class NewsletterSubscriber:
    """One email address on the landing-page newsletter list. Unsubscribing
    keeps the record (with `active: False`) so a later re-subscribe works."""

    @staticmethod
    def _col():
        return mongo.db.newsletter_subscribers

    @classmethod
    def ensure_indexes(cls) -> None:
        cls._col().create_index("email", unique=True)

    @classmethod
    def subscribe(cls, email: str) -> Tuple[str, bool]:
        """Returns (status, is_new_or_returning): "subscribed" for a new or
        re-activated address, "already_subscribed" if it's already on the list."""
        existing = cls._col().find_one({"email": email})
        now = datetime.now(timezone.utc)
        if existing and existing.get("active"):
            return "already_subscribed", False
        if existing:
            cls._col().update_one(
                {"_id": existing["_id"]},
                {"$set": {"active": True, "subscribed_at": now}, "$unset": {"unsubscribed_at": ""}},
            )
        else:
            try:
                cls._col().insert_one({"email": email, "active": True, "subscribed_at": now, "created_at": now})
            except DuplicateKeyError:  # the same address was added a moment ago
                return "already_subscribed", False
        return "subscribed", True

    @classmethod
    def unsubscribe(cls, email: str) -> bool:
        result = cls._col().update_one(
            {"email": email, "active": True},
            {"$set": {"active": False, "unsubscribed_at": datetime.now(timezone.utc)}},
        )
        return result.modified_count > 0

    @classmethod
    def count_active(cls) -> int:
        return cls._col().count_documents({"active": True})

    @classmethod
    def all_active(cls) -> List[Dict[str, Any]]:
        cursor = cls._col().find({"active": True}).sort("subscribed_at", -1)
        return [{"email": doc["email"], "subscribed_at": iso(doc.get("subscribed_at"))} for doc in cursor]
