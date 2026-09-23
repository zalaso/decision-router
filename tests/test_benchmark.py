import pytest

from benchmarks.run import metrics


def test_metrics_known_calibration_example():
    rows = [
        {
            "predicted": "a",
            "status": "decided",
            "correct": True,
            "top_probability": 0.8,
            "brier": 0.08,
            "nll": 0.22314355,
            "latency_ms": 10,
            "agreement": True,
        },
        {
            "predicted": "b",
            "status": "human_review",
            "correct": False,
            "top_probability": 0.8,
            "brier": 1.28,
            "nll": 1.6094379,
            "latency_ms": 20,
            "agreement": False,
        },
    ]
    result = metrics(rows)
    assert result["accuracy_all"] == 0.5
    assert result["coverage"] == 0.5
    assert result["agreement"] == 0.5
    assert result["brier_multiclass"] == pytest.approx(0.68)
    assert result["ece_10_bins"] == pytest.approx(0.3)
    assert result["latency_ms_p95"] == 20


def test_metrics_no_predictions_does_not_invent_scores():
    result = metrics(
        [
            {
                "predicted": None,
                "status": "human_review",
                "correct": False,
                "latency_ms": 1,
                "agreement": None,
            }
        ]
    )
    assert result["answered"] == 0
    assert result["ece_10_bins"] is None
    assert result["agreement"] is None
