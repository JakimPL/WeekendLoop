import json

import pytest
from pydantic import ValidationError

from weekend_loop.models import (
    MAX_QUESTIONS_PER_ASSESSMENT,
    Assessment,
    Blocker,
    Confidence,
    Delivery,
    DeliveryStatus,
    Effort,
    Risk,
    StructuredOutput,
    Verdict,
)
from weekend_loop.schemas import STRUCTURED_OUTPUT_MODELS, structured_output_schema


def sample_assessment() -> Assessment:
    return Assessment(
        verdict=Verdict.PROPOSE,
        effort=Effort.S,
        risk=Risk.REFACTOR,
        blockers=[Blocker.NO_ACCEPTANCE_CRITERIA],
        plan="Extract the interval list into configuration.",
        touched_paths=["example_package/config/dataset.py"],
        questions=["Which deployments carry the exclusions?"],
        confidence=Confidence.MEDIUM,
        depends_on=[4],
    )


@pytest.mark.parametrize("model", list(STRUCTURED_OUTPUT_MODELS.values()))
def test_structured_output_schemas_are_closed_and_fully_required(
    model: type[StructuredOutput],
) -> None:
    schema = structured_output_schema(model)
    assert schema["additionalProperties"] is False
    assert sorted(schema["required"]) == sorted(schema["properties"])


def test_assessment_round_trips_through_json() -> None:
    assessment = sample_assessment()
    assert Assessment.model_validate_json(assessment.model_dump_json()) == assessment


def test_assessment_rejects_extra_keys() -> None:
    payload = json.loads(sample_assessment().model_dump_json())
    payload["reasoning"] = "unexpected"
    with pytest.raises(ValidationError):
        Assessment.model_validate(payload)


def test_assessment_caps_the_number_of_questions() -> None:
    payload = json.loads(sample_assessment().model_dump_json())
    payload["questions"] = ["q"] * (MAX_QUESTIONS_PER_ASSESSMENT + 1)
    with pytest.raises(ValidationError):
        Assessment.model_validate(payload)


def test_delivery_is_frozen() -> None:
    delivery = Delivery(
        status=DeliveryStatus.DONE,
        commit_subject="fix(geometry): normalise bearings to [0, 360)",
        summary="Normalised bearings to [0, 360).",
        verification="uv run pytest tests",
        judgement_calls=[],
        questions=[],
        files_changed=["logbook/geometry.py"],
        confidence=Confidence.HIGH,
    )
    with pytest.raises(ValidationError):
        delivery.summary = "changed"  # type: ignore[misc]


def test_an_assessment_written_before_dependencies_still_loads() -> None:
    written = sample_assessment().model_dump(mode="json")
    del written["depends_on"]
    assert Assessment.model_validate(written).depends_on == []
