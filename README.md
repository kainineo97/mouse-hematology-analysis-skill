# Mouse Hematology Analysis Skill

A reusable Codex skill for organizing longitudinal complete blood count (CBC) data from mice or other experimental animals. It matches analyzer-exported CSV rows to an initial animal registry, applies user-supplied PBS dilution corrections, distinguishes post-death missingness from unexpected missing samples, and produces audit-ready tables, status-free copy/paste tables, and GraphPad Prism-ready TSV files.

## Features

- Builds a fixed animal registry from cage number, tag number, and experimental group.
- Supports custom timepoints, a configurable baseline, and multiple hematology metrics.
- Applies direct dilution multipliers using `corrected_value = raw_value × dilution_factor`.
- Calculates `%D0 = corrected_current / corrected_D0 × 100` by default.
- Distinguishes `DEAD`, `MISSING_SAMPLE`, `MISSING_METRIC`, duplicate samples, non-numeric values, and post-death measurements.
- Produces both audit-oriented wide tables and status-free `copy_ready` tables.
- Produces one Prism XY table per metric, with a user-defined left-to-right group order and stable animal replicate columns across timepoints.
- Preserves input hashes, source locations, raw values, dilution factors, corrected values, and QC records for traceability.
- Reads UTF-8, UTF-8 with BOM, and GB18030 CSV exports.

This project performs experimental data organization only. It does not provide clinical interpretation, survival analysis, or missing-value imputation.

## Repository Layout

```text
mouse-hematology-analysis/
├── README.md
├── LICENSE
├── SKILL.md
├── agents/
│   └── openai.yaml
├── scripts/
│   ├── analyze_hematology.py
│   ├── init_project.py
│   └── test_analyze_hematology.py
├── references/
│   ├── dilution-correction.md
│   ├── graphpad-prism-layout.md
│   ├── input-output-contract.md
│   └── workbook-layout.md
└── examples/
    └── minimal/
```

`SKILL.md` is the Codex entry point. Detailed, task-specific rules are stored under `references/`, and the deterministic data-processing utilities are under `scripts/`. The scripts use only the Python standard library.

## Install as a Codex Skill

Clone or download this repository, then place the entire `mouse-hematology-analysis` directory in your Codex skills directory. Keep `SKILL.md` at the repository root.

A typical personal installation path is:

```text
~/.codex/skills/mouse-hematology-analysis/
```

You can then invoke the skill explicitly in a task:

```text
Use $mouse-hematology-analysis to analyze these longitudinal CBC exports.
```

The [OpenAI Skills API](https://developers.openai.com/api/reference/python/resources/skills/methods/create) also accepts skill files as a directory upload or a single ZIP archive.

## Requirements

- Python 3.10 or later
- No third-party Python packages

## Quick Start

Create a project template:

```bash
python scripts/init_project.py my-study \
  --timepoints D0,D1,D3,D7,D14 \
  --metrics WBC,Neu#,Lym#,RBC,HGB,PLT
```

Inspect metric-like columns in one or more analyzer exports:

```bash
python scripts/analyze_hematology.py \
  --list-metrics my-study/raw/D0.csv my-study/raw/D1.csv
```

Run the analysis:

```bash
python scripts/analyze_hematology.py \
  --config my-study/analysis_config.json \
  --output-dir my-study/output \
  --group-order "PBS,XJ,PB2,CD34" \
  --strict
```

Run the bundled synthetic example:

```bash
python scripts/analyze_hematology.py \
  --config examples/minimal/analysis_config.json \
  --output-dir examples/minimal/output \
  --strict
```

## Required Inputs

Each analysis project uses:

1. `analysis_config.json` — timepoints, source files, metrics, baseline, matching rules, and an optional left-to-right GraphPad group order.
2. `animal_registry.csv` — one row per animal, including cage, tag, group, optional notes, and optional death date.
3. Analyzer-exported CSV files — one or more files per timepoint.
4. `sample_dilutions.csv` — optional dilution records; leave it with only the header when no samples were diluted.

The initializer creates templates for the first, second, and fourth files without overwriting existing files.

## Key Configuration

```json
{
  "baseline_timepoint": "D0",
  "percent_mode": "percent_of_baseline",
  "group_order": ["PBS", "XJ", "PB2", "CD34"],
  "metrics": ["WBC", "Neu#", "Lym#", "RBC", "HGB", "PLT"]
}
```

- Set `group_order` to the desired left-to-right GraphPad dataset order. A user instruction such as `PBS → XJ → PB2 → CD34` maps directly to `["PBS", "XJ", "PB2", "CD34"]`.
- Omit `group_order` or set it to `[]` to use the order in which groups first appear in the animal registry. The workflow never sorts groups alphabetically or reuses another study's order.
- When supplied, `group_order` must list every registry group exactly once. It changes group-summary and GraphPad block order without changing animal order within each group.
- For a one-off override without editing JSON, add `--group-order "PBS,XJ,PB2,CD34"`; the effective order and whether it came from the registry, config, or command line are recorded in `run_manifest.json`.
- `percent_mode` defaults to `percent_of_baseline`. The alternative `percent_change` calculates `(current - baseline) / baseline × 100`.
- `death_date_inclusive` defaults to `true`, meaning that a collection date equal to the recorded death date is treated as post-death. Change this only when the experimental record supports a different boundary.

See the [input, status, and output contract](references/input-output-contract.md) for the complete schema.

## PBS Dilution Correction

Enter a direct numeric correction multiplier in `sample_dilutions.csv`. For example, if the instrument result must be multiplied by two, enter `2`.

```csv
animal_id,timepoint,dilution_factor,metrics,notes
11,D1,2,,Sample diluted twofold with PBS
```

Do not enter ambiguous ratio notation such as `1:1`. The workflow applies dilution correction before D0 normalization and retains the raw value, multiplier, and corrected value separately.

## Output Files

| File | Purpose |
|---|---|
| `animal_registry_normalized.csv` | Validated and normalized animal registry |
| `sample_dilutions_normalized.csv` | Validated dilution records and applied metrics |
| `group_summary.csv` | Initial group sizes, animal IDs, cages, and recorded deaths |
| `source_values.csv` | Raw values, dilution factors, corrected values, and source locations |
| `results_long.csv` | Audit table with one animal × timepoint × metric per row |
| `<metric>_wide.csv` | Metric-specific wide table with status columns |
| `<metric>_copy_ready.csv` | Status-free value and `%D0` table for pasting into an existing worksheet |
| `graphpad_<metric>.tsv` | Prism XY table with groups as datasets and animals as replicate subcolumns |
| `qc_issues.csv` | Missingness, conflicts, baseline problems, and dilution notices |
| `run_manifest.json` | Configuration snapshot, formulas, input hashes, and record counts |

Inspect `qc_issues.csv` before interpreting or plotting results. With `--strict`, the script still writes traceable outputs but returns a non-zero exit code when QC contains an `ERROR`.

## GraphPad Prism Layout

Each `graphpad_<metric>.tsv` contains:

- one numeric X column named `Time (days)`;
- one Y dataset per experimental group;
- one fixed replicate subcolumn per animal;
- the same replicate width for every group, padded with blank columns where needed;
- numeric `%D0` values or blank cells only.

Dataset blocks appear from left to right in the effective user-selected order. Before pasting, verify the first TSV row against `run_manifest.json`.

Missing, invalid, duplicated, post-death, or non-normalizable observations remain blank rather than being written as `0`, `NA`, or an imputed value. A single configured timepoint produces one data row; multiple timepoints produce one row each in configuration order.

## Tests

Run the regression suite from the repository root:

```bash
python -m unittest discover -s scripts -p "test_*.py"
```

The tests cover longitudinal matching, GB18030 input, dilution correction, D0 normalization, status handling, GraphPad layout, configurable group order, status-free copy-ready output, and non-destructive project initialization.

## Data and Publishing Notes

- Do not commit real experimental exports, identifying local paths, or generated analysis outputs to a public repository.
- Everything under `examples/minimal/` is synthetic and is included only to demonstrate and validate the workflow.

## License

This project is released under the [MIT License](LICENSE).
