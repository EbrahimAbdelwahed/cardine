"""Read-only Jev routing calibration report from a study repository (ADR-0027).

Prints counts, closed failure codes, route probability quantiles and what-if
acceptance for candidate thresholds. Reads only routing receipts; never source
text, canonical events, credentials or the network.

    .venv/bin/python scripts/report_routing_calibration.py --repository PATH \\
        --threshold 0.8:0.2 --threshold 0.6:0.15
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from cardine.application.routing_calibration import calibration_report, load_receipts
from cardine.hosts.routing import RoutingThreshold


def _threshold(value: str) -> RoutingThreshold:
    probability, _, margin = value.partition(":")
    try:
        return RoutingThreshold(float(probability), float(margin))
    except ValueError as error:
        message = "threshold must be PROBABILITY:MARGIN in [0, 1]"
        raise argparse.ArgumentTypeError(message) from error


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repository", type=Path, required=True)
    parser.add_argument("--threshold", type=_threshold, action="append", default=[])
    args = parser.parse_args()
    receipts = load_receipts(args.repository / "state" / "runs.sqlite3")
    candidates = tuple(args.threshold) or (RoutingThreshold(0.8, 0.2),)
    print(json.dumps(calibration_report(receipts, candidates), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
