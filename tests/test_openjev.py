import pytest
from pydantic import ValidationError

from decision_router.config import Settings
from decision_router.domain import Candidate, Choice, DecisionRequest, ErrorCode, ProviderError
from decision_router.providers.openjev import OpenJevProvider, _convert, _prepare


def wire_response():
    return {
        "answers": {
            "route": {
                "type": "choice",
                "choice": "A",
                "probabilities": {"A": 0.7, "B": 0.3},
                "confidence": 0.2,
            },
            "code": {"type": "noul", "noul": 0.8},
            "risk": {
                "type": "score",
                "score": 1.0,
                "legend": {"0": "low", "1": "medium", "2": "high"},
                "probabilities": {"0": 0.3333, "1": 0.3333, "2": 0.3333},
                "confidence": 0.0,
            },
        }
    }


def test_prepare_maps_opaque_candidate_ids_to_short_labels(request_data):
    payload, label_maps, labels = _prepare(request_data, 64)
    assert label_maps["route"] == {"A": "coder", "B": "researcher"}
    assert labels["route"] == ["A", "B"]
    assert labels["code"] == ["yes", "no"]
    assert labels["risk"] == ["0 (low)", "1 (medium)", "2 (high)"]
    assert "A: Write code" in payload["questions"]["route"]["instructions"]
    assert payload["state"]["prompt"] == "Write Python code"


def test_convert_normalizes_upstream_rounding_and_preserves_confidence(request_data):
    _, maps, _ = _prepare(request_data, 64)
    result = _convert(request_data, wire_response(), maps, "openjev fixture", 1)
    assert result.provider == "openjev_hf"
    assert result.answers["route"].selected == "coder"
    assert result.answers["route"].confidence == 0.2
    assert result.answers["code"].probabilities["true"] == 0.8
    assert sum(result.answers["risk"].probabilities.values()) == pytest.approx(1)
    assert result.answers["risk"].score == pytest.approx(1)
    assert any("not_calibrated" in warning for warning in result.warnings)


@pytest.mark.parametrize("mutation", ["missing", "score", "probabilities", "kind"])
def test_convert_rejects_bad_upstream_response(request_data, mutation):
    _, maps, _ = _prepare(request_data, 64)
    wire = wire_response()
    if mutation == "missing":
        del wire["answers"]["code"]
    elif mutation == "score":
        wire["answers"]["risk"]["score"] = 9
    elif mutation == "probabilities":
        wire["answers"]["route"]["probabilities"] = {"A": 0.8, "B": 0.3}
    else:
        wire["answers"]["route"] = wire["answers"]["risk"]
    with pytest.raises(ProviderError) as exc:
        _convert(request_data, wire, maps, "openjev fixture", 1)
    assert exc.value.code == ErrorCode.INVALID_RESPONSE


def test_rejects_more_than_26_choice_labels_and_question_temperature():
    request = DecisionRequest(
        prompt="route",
        questions=[
            Choice(
                id="route",
                instructions="Choose",
                candidates=[
                    Candidate(id=f"id_{i}", description=f"candidate {i}") for i in range(27)
                ],
            )
        ],
    )
    with pytest.raises(ProviderError) as exc:
        _prepare(request, 64)
    assert exc.value.code == ErrorCode.UNSUPPORTED
    with pytest.raises(ValidationError):
        Settings(local_backend="openjev", temperature_by_question={"route": 2})

    too_long = request.model_copy(update={"prompt": "x" * 13000})
    with pytest.raises(ProviderError, match="unsupported_input"):
        _prepare(too_long, 64)


def test_upstream_openjev_library_contract_without_weights(request_data):
    upstream = pytest.importorskip("openjev.backends.mock")

    class MockWithUniqueLabels(upstream.MockBackend):
        def _first_token_id(self, label: str) -> int:
            return ord(label[0])

    provider = OpenJevProvider(
        model="fixture", revision="a" * 40, local_files_only=True, temperature=1, max_pairs=64
    )
    provider._backend = MockWithUniqueLabels()
    result = provider._decide_sync(request_data)
    assert set(result.answers) == {"route", "code", "risk"}
    assert result.provider == "openjev_hf"

    class MockWithCollidingLabels(upstream.MockBackend):
        def _first_token_id(self, label: str) -> int:
            return 1

    provider._backend = MockWithCollidingLabels()
    with pytest.raises(ProviderError) as exc:
        provider._decide_sync(request_data)
    assert exc.value.code == ErrorCode.UNSUPPORTED
