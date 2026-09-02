#!/usr/bin/env python3
"""Create non-destructive project templates for mouse hematology analysis."""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path


DEFAULT_TIMEPOINTS = "D0,D1,D3,D7,D14,D21,D28"


def parse_csv_list(value: str) -> list[str]:
    items = [item.strip() for item in value.split(",") if item.strip()]
    if not items:
        raise argparse.ArgumentTypeError("at least one comma-separated value is required")
    if len(items) != len(set(items)):
        raise argparse.ArgumentTypeError("values must be unique")
    return items


def infer_day(label: str) -> int | None:
    match = re.fullmatch(r"[Dd](\d+)", label.strip())
    return int(match.group(1)) if match else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create animal_registry.csv and analysis_config.json templates."
    )
    parser.add_argument("project_dir", type=Path, help="new or empty project directory")
    parser.add_argument(
        "--timepoints",
        type=parse_csv_list,
        default=parse_csv_list(DEFAULT_TIMEPOINTS),
        help=f"comma-separated labels (default: {DEFAULT_TIMEPOINTS})",
    )
    parser.add_argument(
        "--metrics",
        type=parse_csv_list,
        default=["PLT"],
        help="comma-separated metric aliases or exact analyzer headers (default: PLT)",
    )
    parser.add_argument("--project-name", default="mouse-hematology-study")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    project_dir = args.project_dir.expanduser().resolve()
    registry_path = project_dir / "animal_registry.csv"
    dilution_path = project_dir / "sample_dilutions.csv"
    config_path = project_dir / "analysis_config.json"
    collisions = [
        path for path in (registry_path, dilution_path, config_path) if path.exists()
    ]
    if collisions:
        print(
            "Refusing to overwrite existing template(s): "
            + ", ".join(str(path) for path in collisions),
            file=sys.stderr,
        )
        return 2

    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "raw").mkdir(exist_ok=True)

    with registry_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["cage", "tag", "animal_id", "group", "notes", "death_date"])

    with dilution_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["animal_id", "timepoint", "dilution_factor", "metrics", "notes"])

    timepoints = []
    for label in args.timepoints:
        timepoints.append(
            {
                "label": label,
                "day": infer_day(label),
                "collection_date": "",
                "sources": [f"raw/{label}.csv"],
            }
        )

    config = {
        "project_name": args.project_name,
        "animal_registry": "animal_registry.csv",
        "sample_dilutions": "sample_dilutions.csv",
        "baseline_timepoint": args.timepoints[0],
        "animal_id_mode": "cage_tag_concat",
        "max_tag_number": 5,
        "group_order": [],
        "death_date_inclusive": True,
        "percent_mode": "percent_of_baseline",
        "sample_id_column": "样本编号",
        "date_column": "日期",
        "excluded_sample_ids": ["Background"],
        "metrics": args.metrics,
        "timepoints": timepoints,
    }
    with config_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)
        handle.write("\n")

    print(f"Created {registry_path}")
    print(f"Created {dilution_path}")
    print(f"Created {config_path}")
    print(
        "Fill the registry, collection dates, raw CSV paths, and any dilution multipliers "
        "before analysis. Leave sample_dilutions.csv with only its header when none were used."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
