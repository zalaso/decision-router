import pytest

from decision_router.calibration import (
    LabeledPrediction,
    calibration_report,
    fit_temperature,
    rescale,
)


def sample(index, split, label, first):
    return LabeledPrediction(
        sample_id=str(index),
        split=split,
        model="fixture",
        revision="rev",
        question_id="route",
        kind="choice",
        expected=label,
        probabilities={"a": first, "b": 1 - first},
    )


def test_fitting_uses_calibration_and_reports_held_out_metrics():
    rows = [sample(i, "calibration", "a", 0.65) for i in range(10)]
    rows += [sample(i + 10, "test", "b", 0.4) for i in range(10)]
    report = calibration_report(rows)
    assert report["fitted_temperature"] < 1
    assert report["test_after"]["count"] == 10
    assert report["test_after"]["accuracy"] == 1
    assert report["test_before"]["nll"] != report["test_after"]["nll"]
    assert not report["production_ready"]


def test_split_leakage_and_mixed_models_rejected():
    rows = [sample(1, "calibration", "a", 0.7), sample(1, "test", "a", 0.7)]
    with pytest.raises(ValueError, match="unique"):
        calibration_report(rows)
    rows[1] = rows[1].model_copy(update={"sample_id": "2", "model": "other"})
    with pytest.raises(ValueError, match="one model"):
        calibration_report(rows)
    with pytest.raises(ValueError, match="calibration samples only"):
        fit_temperature(rows)


def test_positive_detection_and_invalid_inputs():
    rows = [sample(1, "calibration", "a", 0.7), sample(2, "test", "b", 0.1)]
    report = calibration_report(rows, positive_labels={"b"})
    assert report["test_after"]["positive_detection"][0]["tp"] == 1
    with pytest.raises(ValueError, match="Positive labels"):
        calibration_report(rows, positive_labels={"missing"})
    with pytest.raises(ValueError):
        rescale({"a": 0.5, "b": 0.5}, 0)
