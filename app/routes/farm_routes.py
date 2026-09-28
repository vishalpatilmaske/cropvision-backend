from flask import Blueprint, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.models.crop import Crop
from app.models.farm import Farm
from app.utils.responses import error_response, success_response

farm_bp = Blueprint("farms", __name__, url_prefix="/api/farms")


@farm_bp.get("")
@jwt_required()
def list_farms():
    user_id = get_jwt_identity()
    farms = Farm.find_by_user(user_id)
    return success_response({"farms": [f.to_dict() for f in farms]})


@farm_bp.post("")
@jwt_required()
def create_farm():
    user_id = get_jwt_identity()
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    if not name:
        return error_response("VALIDATION_ERROR", "name is required.", 400)

    farm = Farm.create(
        user_id=user_id,
        name=name,
        location_text=payload.get("location_text"),
        latitude=payload.get("latitude"),
        longitude=payload.get("longitude"),
        area_acres=payload.get("area_acres"),
        soil_type=payload.get("soil_type"),
    )
    return success_response({"farm": farm.to_dict()}, "Farm created.", 201)


@farm_bp.get("/<farm_id>")
@jwt_required()
def get_farm(farm_id: str):
    user_id = get_jwt_identity()
    farm = Farm.find_one_for_user(farm_id, user_id)
    if not farm:
        return error_response("NOT_FOUND", "Farm not found.", 404)
    return success_response({"farm": farm.to_dict()})


@farm_bp.post("/<farm_id>/crops")
@jwt_required()
def add_crop(farm_id: str):
    user_id = get_jwt_identity()
    farm = Farm.find_one_for_user(farm_id, user_id)
    if not farm:
        return error_response("NOT_FOUND", "Farm not found.", 404)

    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    if not name:
        return error_response("VALIDATION_ERROR", "name is required.", 400)

    crop = Crop.create(
        farm_id=farm.id,
        name=name,
        variety=payload.get("variety"),
        growth_stage=payload.get("growth_stage"),
    )
    return success_response({"crop": crop.to_dict()}, "Crop added.", 201)
