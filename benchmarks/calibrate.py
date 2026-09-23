"""Fit on a labeled calibration split and evaluate once on a held-out test split."""

import argparse
import json
from pathlib import Path

from pydantic import ValidationError

from decision_router.calibration import LabeledPrediction, calibration_report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--positive-label", action="append", default=[])
    args = parser.parse_args()
    try:
        samples = [
            LabeledPrediction.model_validate_json(line)
            for line in args.data.read_text(encoding="utf-8").splitlines()
            if line
        ]
        report = calibration_report(samples, positive_labels=set(args.positive_label))
    except (OSError, ValueError, ValidationError):
        parser.exit(2, "Invalid labeled data or split; see docs/CALIBRATION.md.\n")
    output = json.dumps(report, indent=2, allow_nan=False)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
