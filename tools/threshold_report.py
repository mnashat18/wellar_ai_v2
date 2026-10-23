"""Summarize rule-state and raw-measure distributions in an owner-supplied folder.

This tool reads only the explicitly supplied folder. It does not score accuracy,
compare against labels, or access any other path.
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path


def summarize(folder: Path) -> dict:
    states: dict[str, int] = {}
    values: dict[str, list[float]] = {}
    for path in sorted(folder.glob("*.json")):
        record = json.loads(path.read_text(encoding="utf-8"))
        indicator = record.get("drowsiness_indicator", record)
        state = indicator.get("state", indicator.get("status", "missing"))
        states[state] = states.get(state, 0) + 1
        for name, value in indicator.get("raw_measurements", {}).items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                values.setdefault(name, []).append(float(value))
    distributions = {}
    for name, items in values.items():
        ordered = sorted(items)
        distributions[name] = {
            "n": len(items), "min": min(items), "median": statistics.median(items),
            "max": max(items),
        }
    return {"label": "descriptive distribution only; no accuracy or validation claim", "records": sum(states.values()), "state_counts": states, "raw_measure_distributions": distributions}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--folder", required=True, type=Path, help="owner-supplied folder containing indicator JSON files")
    args = parser.parse_args()
    if not args.folder.is_dir():
        parser.error("folder must exist")
    print(json.dumps(summarize(args.folder), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
