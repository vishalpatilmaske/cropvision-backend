import json

import pytest

from app.services.ai import disease_pest_service
from app.services.ai.disease_pest_service import InvalidAIResponseError, analyze_crop_image
from app.services.ai.llm_client import AIResponse, AIServiceError

VALID_PAYLOAD = {
    "crop": {"name": "Tomato", "confidence": 80},
    "analysis": {
        "type": "disease",
        "name": "Late Blight",
        "confidence": 65,
        "severity": "medium",
        "assessment_level": "likely",
        "symptoms": ["dark lesions on leaves", "white fungal growth on undersides"],
        "possible_causes": ["Phytophthora infestans", "prolonged leaf wetness"],
    },
    "recommendations": {
        "immediate_action": ["Remove and destroy affected leaves"],
        "treatment": ["Apply a copper-based fungicide"],
        "prevention": ["Avoid overhead irrigation", "Improve air circulation"],
    },
    "additional_observations": ["Nearby leaves look healthy"],
    "needs_expert_confirmation": False,
}


def test_analyze_crop_image_success(monkeypatch):
    monkeypatch.setattr(
        disease_pest_service.llm_client,
        "generate_structured_json_from_image",
        lambda **kwargs: AIResponse(text=json.dumps(VALID_PAYLOAD), model="gpt-4o", latency_ms=120),
    )

    result = analyze_crop_image(b"fake-bytes", "image/jpeg", crop_name="Tomato")
    assert result["analysis"]["type"] == "disease"
    assert result["analysis"]["name"] == "Late Blight"
    assert result["needs_expert_confirmation"] is False


def test_analyze_crop_image_invalid_json_raises(monkeypatch):
    monkeypatch.setattr(
        disease_pest_service.llm_client,
        "generate_structured_json_from_image",
        lambda **kwargs: AIResponse(text="not json at all", model="gpt-4o", latency_ms=50),
    )
    with pytest.raises(InvalidAIResponseError):
        analyze_crop_image(b"fake-bytes", "image/jpeg")


def test_analyze_crop_image_missing_fields_raises(monkeypatch):
    bad_payload = {"crop": {"name": "Tomato"}}  # missing analysis/recommendations
    monkeypatch.setattr(
        disease_pest_service.llm_client,
        "generate_structured_json_from_image",
        lambda **kwargs: AIResponse(text=json.dumps(bad_payload), model="gpt-4o", latency_ms=50),
    )
    with pytest.raises(InvalidAIResponseError):
        analyze_crop_image(b"fake-bytes", "image/jpeg")


def test_analyze_crop_image_unknown_confidence_clamped(monkeypatch):
    payload = json.loads(json.dumps(VALID_PAYLOAD))
    payload["analysis"]["assessment_level"] = "unknown"
    payload["analysis"]["confidence"] = 95
    monkeypatch.setattr(
        disease_pest_service.llm_client,
        "generate_structured_json_from_image",
        lambda **kwargs: AIResponse(text=json.dumps(payload), model="gpt-4o", latency_ms=50),
    )
    result = analyze_crop_image(b"fake-bytes", "image/jpeg")
    assert result["analysis"]["confidence"] <= 40


def test_ai_service_error_propagates(monkeypatch):
    def _raise(**kwargs):
        raise AIServiceError("timed out", code="AI_TIMEOUT")

    monkeypatch.setattr(disease_pest_service.llm_client, "generate_structured_json_from_image", _raise)
    with pytest.raises(AIServiceError) as exc:
        analyze_crop_image(b"fake-bytes", "image/jpeg")
    assert exc.value.code == "AI_TIMEOUT"


def _analyze_with(monkeypatch, payload):
    monkeypatch.setattr(
        disease_pest_service.llm_client,
        "generate_structured_json_from_image",
        lambda **kwargs: AIResponse(text=json.dumps(payload), model="gpt-4o", latency_ms=50),
    )
    return analyze_crop_image(b"fake-bytes", "image/jpeg")


def test_report_block_is_normalized(monkeypatch):
    payload = json.loads(json.dumps(VALID_PAYLOAD))
    payload["report"] = {
        "health_score": 140,
        "summary": "  Early blight on lower leaves.  ",
        "affected_area_pct": "15",
        "spread_risk": "extreme",
        "urgency": "within_week",
        "organic_options": ["Neem oil spray", ""],
        "monitoring_plan": "not a list",
    }
    report = _analyze_with(monkeypatch, payload)["report"]
    assert report["health_score"] == 100
    assert report["summary"] == "Early blight on lower leaves."
    assert report["affected_area_pct"] == 15
    assert report["spread_risk"] is None
    assert report["urgency"] == "within_week"
    assert report["organic_options"] == ["Neem oil spray"]
    assert report["monitoring_plan"] == []


def test_missing_report_block_gets_defaults(monkeypatch):
    report = _analyze_with(monkeypatch, json.loads(json.dumps(VALID_PAYLOAD)))["report"]
    assert report["health_score"] is None
    assert report["urgency"] == "monitor"
