"""Small shared helpers for working with MongoDB documents."""
import re
from datetime import timezone
from typing import Optional

from bson import ObjectId
from bson.errors import InvalidId


def to_object_id(value) -> Optional[ObjectId]:
    """Safely parse a value (route param, form field, JSON field) into an
    ObjectId. Returns None instead of raising when the value is missing or
    not a valid ObjectId, so callers can treat it as "not found" rather than
    crashing on a malformed id."""
    if not value:
        return None
    if isinstance(value, ObjectId):
        return value
    try:
        return ObjectId(str(value))
    except (InvalidId, TypeError):
        return None


def iso(dt) -> Optional[str]:
    """ISO-format a datetime, tolerating None. PyMongo returns naive UTC
    datetimes; tag them as UTC so browsers don't read them as local time."""
    if not dt:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def contains_regex(text: str) -> dict:
    """Case-insensitive "contains" match for user-typed search text. The text
    is escaped so characters like "(" or "+" can't break or abuse the regex."""
    return {"$regex": re.escape(text), "$options": "i"}
