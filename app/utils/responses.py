from typing import Any, Optional

from flask import jsonify


def success_response(data: Any = None, message: str = "", status_code: int = 200):
    body = {"success": True, "data": data if data is not None else {}, "message": message}
    return jsonify(body), status_code


def error_response(code: str, message: str, status_code: int = 400, details: Optional[Any] = None):
    error = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return jsonify({"success": False, "error": error}), status_code
