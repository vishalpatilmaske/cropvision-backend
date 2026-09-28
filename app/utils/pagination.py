import math
from typing import Any, Dict, List


def get_pagination_params(default_per_page: int = 20, max_per_page: int = 100):
    from flask import request

    try:
        page = max(1, int(request.args.get("page", 1)))
    except (TypeError, ValueError):
        page = 1
    try:
        per_page = int(request.args.get("per_page", default_per_page))
    except (TypeError, ValueError):
        per_page = default_per_page
    per_page = max(1, min(per_page, max_per_page))
    return page, per_page


def paginated_response(items: List[Any], total: int, page: int, per_page: int, serialize) -> Dict[str, Any]:
    """Builds the {items, pagination} envelope from an already-fetched page of Mongo documents plus
    the total matching count (each model's `find_paginated` returns both)."""
    total_pages = max(1, math.ceil(total / per_page)) if total else 0
    return {
        "items": [serialize(item) for item in items],
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total_items": total,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1,
        },
    }
