#!/usr/bin/env python3
"""Regression tests for the deterministic hematology helpers."""

from __future__ import annotations

import csv
import json
import shutil
import unittest
import uuid
from contextlib import contextmanager
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Iterator

from analyze_hematology import (
    Animal,
    MetricSpec,
    TimepointData,
    UserInputError,
    build_graphpad_rows,
    load_dilutions,
    main as analyze_main,
    run_analysis,
)
from init_project import main as init_project_main


TEST_TEMP_ROOT = Path(__file__).resolve().parent / ".test_tmp"


@contextmanager
def workspace_temporary_directory() -> Iterator[str]:
    TEST_TEMP_ROOT.mkdir(exist_ok=True)
    path = TEST_TEMP_ROOT / f"case_{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield str(path)
    finally:
        shutil.rmtree(path)


def tearDownModule() -> None:  # noqa: N802 - unittest hook name
    try:
        TEST_TEMP_ROOT.rmdir()
    except OSError:
        pass


def write_csv(path: Path, rows: list[list[str]], encoding: str) -> None:
    with path.open("w", encoding=encoding, newline="") as handle:
        csv.writer(handle).writerows(rows)


def read_dicts(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


class HematologyAnalysisTests(unittest.TestCase):
    def test_end_to_end_statuses_percentages_and_gb18030(self) -> None:
        with workspace_temporary_directory() as temp_dir:
            root = Path(temp_dir)
            registry = root / "animal_registry.csv"
            dilutions = root / "sample_dilutions.csv"
            d0 = root / "D0.csv"
            d1 = root / "D1.csv"
            config = root / "analysis_config.json"
            output = root / "output"

            write_csv(
                registry,
                [
                    ["cage", "tag", "animal_id", "group", "notes", "death_date"],
                    ["1", "0", "", "PBS", "", ""],
                    ["1", "1", "", "Drug", "", "2026-01-02"],
                    ["1", "2", "", "Drug", "", ""],
                    ["1", "3", "", "Cells", "", ""],
                    ["1", "4", "", "Cells", "", "2026-01-02"],
                    ["1", "5", "", "PBS", "", ""],
                ],
                "utf-8-sig",
            )
            write_csv(
                dilutions,
                [
                    ["animal_id", "timepoint", "dilution_factor", "metrics", "notes"],
                    ["10", "D1", "2", "", "Tail-vein sample diluted with PBS"],
                ],
                "utf-8-sig",
            )
            raw_header = ["样本编号", "日期", "PLT (10^9/L)", "WBC (10^9/L)"]
            write_csv(
                d0,
                [
                    raw_header,
                    ["10", "2026-01-01", "100", "5.0"],
                    ["11", "2026-01-01", "200", "6.0"],
                    ["12", "2026-01-01", "0", "7.0"],
                    ["14", "2026-01-01", "50", "8.0"],
                    ["15", "2026-01-01", "80", "9.0"],
                    ["Background", "2026-01-01", "1", "0.1"],
                    ["99", "2026-01-01", "70", "4.0"],
                ],
                "gb18030",
            )
            write_csv(
                d1,
                [
                    raw_header,
                    ["10", "2026-01-02", "50", "4.0"],
                    ["12", "2026-01-02", "10", "6.0"],
                    ["13", "2026-01-02", "30", "3.0"],
                    ["14", "2026-01-02", "25", "2.0"],
                    ["15", "2026-01-02", "40", "1.0"],
                    ["15", "2026-01-02", "41", "1.1"],
                    ["Background", "2026-01-02", "1", "0.1"],
                ],
                "gb18030",
            )
            config.write_text(
                json.dumps(
                    {
                        "project_name": "synthetic-test",
                        "animal_registry": registry.name,
                        "sample_dilutions": dilutions.name,
                        "baseline_timepoint": "D0",
                        "animal_id_mode": "cage_tag_concat",
                        "max_tag_number": 5,
                        "group_order": ["Cells", "PBS", "Drug"],
                        "death_date_inclusive": True,
                        "percent_mode": "percent_of_baseline",
                        "sample_id_column": "样本编号",
                        "date_column": "日期",
                        "excluded_sample_ids": ["Background"],
                        "metrics": ["PLT", "WBC"],
                        "timepoints": [
                            {
                                "label": "D0",
                                "day": 0,
                                "collection_date": "2026-01-01",
                                "sources": [d0.name],
                            },
                            {
                                "label": "D1",
                                "day": 1,
                                "collection_date": "2026-01-02",
                                "sources": [d1.name],
                            },
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            manifest, error_count = run_analysis(config, output, overwrite=False)
            self.assertGreaterEqual(error_count, 3)
            self.assertEqual(manifest["counts"]["animals"], 6)
            self.assertEqual(manifest["counts"]["dilution_records"], 1)
            self.assertEqual(manifest["counts"]["long_result_rows"], 24)
            raw_encodings = {
                item["encoding"]
                for item in manifest["input_files"]
                if item["role"] == "raw_measurement"
            }
            self.assertEqual(raw_encodings, {"gb18030"})

            long_rows = read_dicts(output / "results_long.csv")
            index = {
                (row["animal_id"], row["timepoint"], row["metric"]): row
                for row in long_rows
            }
            plt = "PLT (10^9/L)"
            wbc = "WBC (10^9/L)"
            self.assertEqual(index[("10", "D1", plt)]["raw_value"], "50")
            self.assertEqual(index[("10", "D1", plt)]["dilution_factor"], "2")
            self.assertEqual(index[("10", "D1", plt)]["corrected_value"], "100")
            self.assertEqual(index[("10", "D1", plt)]["value"], "100")
            self.assertEqual(index[("10", "D1", plt)]["pct_D0"], "100.00")
            self.assertIn("DILUTION_APPLIED", index[("10", "D1", plt)]["qc_flags"])
            self.assertEqual(index[("10", "D1", wbc)]["corrected_value"], "8")
            self.assertEqual(index[("10", "D1", wbc)]["pct_D0"], "160.00")
            self.assertEqual(index[("11", "D1", plt)]["status"], "DEAD")
            self.assertEqual(index[("13", "D0", plt)]["status"], "MISSING_SAMPLE")
            self.assertEqual(index[("13", "D1", plt)]["pct_D0"], "")
            self.assertEqual(index[("14", "D1", plt)]["status"], "POST_DEATH_DATA")
            self.assertEqual(index[("14", "D1", plt)]["value"], "25")
            self.assertEqual(index[("15", "D1", plt)]["status"], "DUPLICATE_SAMPLE")
            self.assertIn("BASELINE_ZERO", index[("12", "D1", plt)]["qc_flags"])

            qc_codes = {row["code"] for row in read_dicts(output / "qc_issues.csv")}
            self.assertTrue(
                {
                    "BASELINE_UNAVAILABLE",
                    "BASELINE_ZERO",
                    "DUPLICATE_SAMPLE",
                    "DILUTION_CONFIGURED",
                    "MISSING_SAMPLE",
                    "POST_DEATH_DATA",
                    "UNEXPECTED_SAMPLE_ID",
                }.issubset(qc_codes)
            )
            normalized_dilutions = read_dicts(
                output / "sample_dilutions_normalized.csv"
            )
            self.assertEqual(len(normalized_dilutions), 1)
            self.assertEqual(normalized_dilutions[0]["dilution_factor"], "2")
            self.assertEqual(
                normalized_dilutions[0]["metrics"],
                "PLT (10^9/L);WBC (10^9/L)",
            )
            source_rows = read_dicts(output / "source_values.csv")
            source_index = {
                (row["sample_id"], row["timepoint"], row["metric"]): row
                for row in source_rows
            }
            self.assertEqual(source_index[("10", "D1", plt)]["raw_value"], "50")
            self.assertEqual(source_index[("10", "D1", plt)]["corrected_value"], "100")
            group_summary = read_dicts(output / "group_summary.csv")
            group_counts = {
                row["group"]: row["initial_n"]
                for row in group_summary
            }
            self.assertEqual(group_counts, {"Cells": "2", "PBS": "2", "Drug": "2"})
            self.assertEqual(
                [row["group"] for row in group_summary], ["Cells", "PBS", "Drug"]
            )

            copy_ready_rows = read_dicts(output / "PLT_copy_ready.csv")
            self.assertEqual(len(copy_ready_rows), 6)
            self.assertFalse(
                any(column.endswith("_status") for column in copy_ready_rows[0])
            )
            self.assertEqual(copy_ready_rows[0]["D1_2026-01-02_pct_D0"], "100.00")

            with (output / "graphpad_PLT.tsv").open(
                "r", encoding="utf-8-sig", newline=""
            ) as handle:
                graphpad_rows = list(csv.reader(handle, delimiter="\t"))
            self.assertEqual(
                graphpad_rows[0],
                ["Time (days)", "Cells", "", "PBS", "", "Drug", ""],
            )
            self.assertEqual(
                graphpad_rows[1],
                ["X", "13", "14", "10", "15", "11", "12"],
            )
            self.assertEqual(graphpad_rows[2][0], "0")
            self.assertEqual(
                graphpad_rows[2][1:],
                ["", "100.00", "100.00", "100.00", "100.00", ""],
            )
            self.assertEqual(graphpad_rows[3][0], "1")
            self.assertEqual(graphpad_rows[3][1:], ["", "", "100.00", "", "", ""])
            self.assertEqual(
                manifest["metrics"][0]["graphpad_file"], "graphpad_PLT.tsv"
            )
            self.assertEqual(
                manifest["metrics"][0]["copy_ready_file"], "PLT_copy_ready.csv"
            )
            self.assertEqual(manifest["group_order"], ["Cells", "PBS", "Drug"])
            self.assertEqual(manifest["group_order_source"], "config")

    def test_graphpad_single_timepoint_has_one_data_row_and_pads_groups(self) -> None:
        animals = [
            Animal(2, "1", "0", "10", "PBS", "", None),
            Animal(3, "1", "1", "11", "PBS", "", None),
            Animal(4, "2", "0", "20", "Drug", "", None),
        ]
        timepoints = [
            TimepointData("D0", "", date(2026, 1, 1), [], {}),
        ]
        metric = MetricSpec("WBC", "WBC (10^9/L)", "WBC", "10^9/L", "WBC")
        results = [
            {
                "animal_id": animal.animal_id,
                "timepoint": "D0",
                "metric": metric.display_name,
                "pct_D0": Decimal("100.00"),
            }
            for animal in animals
        ]

        rows = build_graphpad_rows(
            animals, timepoints, metric, results, "D0", "pct_D0"
        )

        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0], ["Time (days)", "PBS", "", "Drug", ""])
        self.assertEqual(rows[1], ["X", "10", "11", "20", ""])
        self.assertEqual(
            rows[2],
            [
                "0",
                Decimal("100.00"),
                Decimal("100.00"),
                Decimal("100.00"),
                "",
            ],
        )

    def test_run_group_order_override_controls_graphpad_left_to_right_order(self) -> None:
        with workspace_temporary_directory() as temp_dir:
            output = Path(temp_dir) / "output"
            example_root = Path(__file__).resolve().parents[1] / "examples" / "minimal"

            exit_code = analyze_main(
                [
                    "--config",
                    str(example_root / "analysis_config.json"),
                    "--output-dir",
                    str(output),
                    "--group-order",
                    "Drug,PBS",
                ]
            )
            self.assertEqual(exit_code, 0)
            manifest = json.loads(
                (output / "run_manifest.json").read_text(encoding="utf-8")
            )

            with (output / "graphpad_PLT.tsv").open(
                "r", encoding="utf-8-sig", newline=""
            ) as handle:
                rows = list(csv.reader(handle, delimiter="\t"))
            self.assertEqual(rows[0], ["Time (days)", "Drug", "", "PBS", ""])
            self.assertEqual(rows[1], ["X", "11", "21", "10", "20"])
            self.assertEqual(manifest["group_order"], ["Drug", "PBS"])
            self.assertEqual(manifest["group_order_source"], "command_line")

    def test_dilution_factor_rejects_ratio_notation(self) -> None:
        with workspace_temporary_directory() as temp_dir:
            root = Path(temp_dir)
            dilution_path = root / "sample_dilutions.csv"
            write_csv(
                dilution_path,
                [
                    ["animal_id", "timepoint", "dilution_factor", "metrics", "notes"],
                    ["10", "D0", "1:1", "WBC", "ambiguous ratio"],
                ],
                "utf-8-sig",
            )
            animals = [Animal(2, "1", "0", "10", "PBS", "", None)]
            timepoints = [TimepointData("D0", 0, date(2026, 1, 1), [], {})]
            metrics = [
                MetricSpec("WBC", "WBC (10^9/L)", "WBC", "10^9/L", "WBC")
            ]

            with self.assertRaisesRegex(UserInputError, "numeric multiplier"):
                load_dilutions(
                    {"sample_dilutions": dilution_path.name},
                    root,
                    animals,
                    timepoints,
                    metrics,
                    [],
                )

    def test_group_order_must_be_a_complete_permutation(self) -> None:
        animals = [
            Animal(2, "1", "0", "10", "PBS", "", None),
            Animal(3, "2", "0", "20", "Drug", "", None),
        ]
        metric = MetricSpec("WBC", "WBC (10^9/L)", "WBC", "10^9/L", "WBC")
        timepoints = [TimepointData("D0", 0, date(2026, 1, 1), [], {})]
        results = [
            {
                "animal_id": animal.animal_id,
                "timepoint": "D0",
                "metric": metric.display_name,
                "pct_D0": Decimal("100.00"),
            }
            for animal in animals
        ]
        with self.assertRaisesRegex(UserInputError, "complete permutation"):
            build_graphpad_rows(
                animals,
                timepoints,
                metric,
                results,
                "D0",
                "pct_D0",
                ["PBS"],
            )

    def test_project_initializer_refuses_overwrite(self) -> None:
        with workspace_temporary_directory() as temp_dir:
            project = Path(temp_dir) / "project"
            result = init_project_main(
                [
                    str(project),
                    "--timepoints",
                    "D0,D1",
                    "--metrics",
                    "PLT,WBC",
                    "--project-name",
                    "initializer-test",
                ]
            )
            self.assertEqual(result, 0)
            self.assertTrue((project / "animal_registry.csv").exists())
            self.assertTrue((project / "sample_dilutions.csv").exists())
            config = json.loads((project / "analysis_config.json").read_text(encoding="utf-8"))
            self.assertEqual(config["metrics"], ["PLT", "WBC"])
            self.assertEqual(config["baseline_timepoint"], "D0")
            self.assertEqual(config["sample_dilutions"], "sample_dilutions.csv")
            self.assertEqual(config["group_order"], [])
            self.assertEqual(init_project_main([str(project)]), 2)


if __name__ == "__main__":
    unittest.main()
