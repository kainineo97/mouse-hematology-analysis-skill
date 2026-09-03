#!/usr/bin/env python3
"""Analyze longitudinal animal hematology CSV exports without altering raw files."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import re
import sys
import unicodedata
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Iterable


VERSION = "1.4.0"
PCT_QUANTUM = Decimal("0.01")

METRIC_ALIASES: "OrderedDict[str, str]" = OrderedDict(
    [
        ("WBC", "WBC (10^9/L)"),
        ("Neu#", "Neu# (10^9/L)"),
        ("Lym#", "Lym# (10^9/L)"),
        ("Mon#", "Mon# (10^9/L)"),
        ("Eos#", "Eos# (10^9/L)"),
        ("Bas#", "Bas# (10^9/L)"),
        ("Neu%", "Neu% (%)"),
        ("Lym%", "Lym% (%)"),
        ("Mon%", "Mon% (%)"),
        ("Eos%", "Eos% (%)"),
        ("Bas%", "Bas% (%)"),
        ("RBC", "RBC (10^12/L)"),
        ("HGB", "HGB (g/L)"),
        ("HCT", "HCT (%)"),
        ("MCV", "MCV (fL)"),
        ("MCH", "MCH (pg)"),
        ("MCHC", "MCHC (g/L)"),
        ("RDW-CV", "RDW-CV (%)"),
        ("RDW-SD", "RDW-SD (fL)"),
        ("PLT", "PLT (10^9/L)"),
        ("MPV", "MPV (fL)"),
        ("PDW", "PDW ( )"),
        ("PCT", "PCT (%)"),
        ("P-LCC", "P-LCC (10^9/L)"),
        ("P-LCR", "P-LCR (%)"),
    ]
)

REGISTRY_ALIASES = {
    "cage": ["cage", "cage number", "cage_number", "笼号", "笼编号"],
    "tag": [
        "tag",
        "tag number",
        "tag_number",
        "ear tag",
        "ear_tag",
        "耳标",
        "耳号",
    ],
    "animal_id": ["animal_id", "animal id", "sample_id", "sample id", "样本编号", "编号"],
    "group": ["group", "grouping", "treatment", "分组", "处理组"],
    "notes": ["notes", "note", "备注"],
    "death_date": ["death_date", "death date", "day of death", "死亡日期", "死亡日"],
}

DILUTION_ALIASES = {
    "animal_id": ["animal_id", "animal id", "sample_id", "sample id", "动物编号", "样本编号"],
    "timepoint": ["timepoint", "time point", "时间点"],
    "dilution_factor": [
        "dilution_factor",
        "dilution factor",
        "correction_multiplier",
        "correction multiplier",
        "稀释倍数",
        "校正倍数",
    ],
    "metrics": ["metrics", "metric", "指标", "校正指标"],
    "notes": ["notes", "note", "备注"],
}

NON_METRIC_HEADERS = {
    "样本编号",
    "姓名",
    "模式",
    "日期",
    "时间",
    "样本状态",
    "病历号",
    "性别",
    "病人类型",
    "参考组",
    "出生日期",
    "年龄",
    "科室",
    "床号",
    "采样日期",
    "采样时间",
    "送检日期",
    "送检时间",
    "送检者",
    "检验者",
    "审核者",
    "备注",
}


class UserInputError(Exception):
    """Raised for actionable problems in user-controlled inputs."""


@dataclass(frozen=True)
class CsvRecord:
    record_no: int
    values: dict[str, str]


@dataclass
class CsvTable:
    path: Path
    encoding: str
    delimiter: str
    sha256: str
    headers: list[str]
    original_headers: list[str]
    rows: list[CsvRecord]


@dataclass(frozen=True)
class Animal:
    registry_row: int
    cage: str
    tag: str
    animal_id: str
    group: str
    notes: str
    death_date: date | None


@dataclass(frozen=True)
class MetricSpec:
    requested: str
    display_name: str
    metric_name: str
    unit: str
    slug: str


@dataclass(frozen=True)
class SourceMeasurement:
    sample_id: str
    source_file: str
    source_row: int
    source_date: date | None
    source_date_raw: str
    raw_values: dict[str, str]


@dataclass
class TimepointData:
    label: str
    day: Any
    collection_date: date
    sources: list[Path]
    rows_by_id: dict[str, list[SourceMeasurement]]


@dataclass(frozen=True)
class DilutionSpec:
    source_row: int
    animal_id: str
    timepoint: str
    dilution_factor: Decimal
    metric_display_names: tuple[str, ...]
    notes: str


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    return unicodedata.normalize("NFKC", str(value)).strip()


def normalize_identifier(value: Any) -> str:
    text = normalize_text(value)
    match = re.fullmatch(r"([+-]?\d+)\.0+", text)
    return match.group(1) if match else text


def normalize_header(value: Any) -> str:
    return re.sub(r"\s+", "", normalize_text(value).casefold())


def normalize_key(value: Any) -> str:
    return "".join(ch for ch in normalize_text(value).casefold() if ch.isalnum())


ALIAS_LOOKUP = {
    normalize_header(alias): canonical for alias, canonical in METRIC_ALIASES.items()
}
CANONICAL_TO_ALIAS = {
    normalize_header(canonical): alias for alias, canonical in METRIC_ALIASES.items()
}


def decode_csv_bytes(raw: bytes, path: Path) -> tuple[str, str]:
    candidates = ["utf-8-sig"] if raw.startswith(b"\xef\xbb\xbf") else ["utf-8"]
    candidates.append("gb18030")
    for encoding in candidates:
        try:
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise UserInputError(f"Cannot decode CSV as UTF-8 or GB18030: {path}")


def choose_delimiter(text: str) -> str:
    first_line = text.splitlines()[0] if text.splitlines() else ""
    counts = {delimiter: first_line.count(delimiter) for delimiter in (",", "\t", ";")}
    delimiter, count = max(counts.items(), key=lambda item: item[1])
    return delimiter if count else ","


def make_unique_headers(original: list[str]) -> list[str]:
    seen: Counter[str] = Counter()
    unique: list[str] = []
    for index, header in enumerate(original, start=1):
        base = normalize_text(header) or f"_unnamed_{index}"
        seen[base] += 1
        unique.append(base if seen[base] == 1 else f"{base}__{seen[base]}")
    return unique


def read_csv_table(path: Path) -> CsvTable:
    if not path.exists():
        raise UserInputError(f"Input file does not exist: {path}")
    if not path.is_file():
        raise UserInputError(f"Input path is not a file: {path}")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise UserInputError(f"Cannot read input file {path}: {exc}") from exc
    text, encoding = decode_csv_bytes(raw, path)
    delimiter = choose_delimiter(text)
    parsed = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter))
    if not parsed:
        raise UserInputError(f"CSV is empty: {path}")
    max_columns = max(len(row) for row in parsed)
    original_headers = [normalize_text(value) for value in parsed[0]]
    while len(original_headers) < max_columns:
        original_headers.append("")
    headers = make_unique_headers(original_headers)
    rows: list[CsvRecord] = []
    for record_no, raw_row in enumerate(parsed[1:], start=2):
        padded = raw_row + [""] * (max_columns - len(raw_row))
        if not any(normalize_text(value) for value in padded):
            continue
        rows.append(
            CsvRecord(
                record_no=record_no,
                values={headers[index]: padded[index] for index in range(max_columns)},
            )
        )
    return CsvTable(
        path=path,
        encoding=encoding,
        delimiter=delimiter,
        sha256=hashlib.sha256(raw).hexdigest(),
        headers=headers,
        original_headers=original_headers,
        rows=rows,
    )


def header_pairs(table: CsvTable) -> Iterable[tuple[str, str]]:
    return zip(table.headers, table.original_headers)


def find_exact_header(table: CsvTable, target: str, *, required: bool = True) -> str | None:
    target_norm = normalize_header(target)
    matches = [internal for internal, original in header_pairs(table) if normalize_header(original) == target_norm]
    if len(matches) == 1:
        return matches[0]
    if not matches and not required:
        return None
    if not matches:
        raise UserInputError(f"Column {target!r} not found in {table.path}")
    raise UserInputError(f"Column {target!r} is duplicated in {table.path}")


def find_registry_header(table: CsvTable, field: str, *, required: bool) -> str | None:
    aliases = {normalize_key(alias) for alias in REGISTRY_ALIASES[field]}
    matches = [internal for internal, original in header_pairs(table) if normalize_key(original) in aliases]
    if len(matches) == 1:
        return matches[0]
    if not matches and not required:
        return None
    if not matches:
        raise UserInputError(
            f"Registry column {field!r} not found in {table.path}; supported names: "
            + ", ".join(REGISTRY_ALIASES[field])
        )
    raise UserInputError(f"Registry column {field!r} matches more than once in {table.path}")


def find_dilution_header(table: CsvTable, field: str, *, required: bool) -> str | None:
    aliases = {normalize_key(alias) for alias in DILUTION_ALIASES[field]}
    matches = [internal for internal, original in header_pairs(table) if normalize_key(original) in aliases]
    if len(matches) == 1:
        return matches[0]
    if not matches and not required:
        return None
    if not matches:
        raise UserInputError(
            f"Dilution column {field!r} not found in {table.path}; supported names: "
            + ", ".join(DILUTION_ALIASES[field])
        )
    raise UserInputError(f"Dilution column {field!r} matches more than once in {table.path}")


def parse_date(value: Any, *, context: str) -> date | None:
    text = normalize_text(value)
    if not text:
        return None
    for pattern in ("%Y-%m-%d", "%Y.%m.%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            pass
    raise UserInputError(f"Cannot parse date {text!r} ({context}); use YYYY-MM-DD")


def parse_decimal(value: Any) -> Decimal | None:
    text = normalize_text(value)
    if not text:
        return None
    if re.fullmatch(r"[+-]?\d{1,3}(,\d{3})+(\.\d+)?", text):
        text = text.replace(",", "")
    try:
        result = Decimal(text)
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def decimal_to_string(value: Decimal | None) -> str:
    if value is None:
        return ""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text in {"-0", ""} else text


def format_csv_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return decimal_to_string(value)
    if isinstance(value, date):
        return value.isoformat()
    if value is None:
        return ""
    return value


def format_csv_field(fieldname: str, value: Any) -> Any:
    if isinstance(value, Decimal) and (
        fieldname == "pct_D0"
        or fieldname == "delta_pct_D0"
        or fieldname.endswith("_pct_D0")
        or fieldname.endswith("_delta_pct_D0")
    ):
        return format(value, ".2f")
    return format_csv_value(value)


def metric_name_and_unit(display_name: str) -> tuple[str, str]:
    match = re.fullmatch(r"\s*(.*?)\s*\((.*?)\)\s*", display_name)
    return (match.group(1).strip(), match.group(2).strip()) if match else (display_name, "")


def slugify_metric(value: str) -> str:
    alias = CANONICAL_TO_ALIAS.get(normalize_header(value), value)
    slug = unicodedata.normalize("NFKD", alias).encode("ascii", "ignore").decode("ascii")
    slug = slug.replace("#", "_abs").replace("%", "_pct")
    slug = re.sub(r"[^A-Za-z0-9]+", "_", slug).strip("_")
    return slug or "metric"


def build_metric_specs(tokens: list[Any]) -> list[MetricSpec]:
    if not tokens:
        raise UserInputError("Config metrics must contain at least one metric")
    specs: list[MetricSpec] = []
    seen_display: set[str] = set()
    used_slugs: Counter[str] = Counter()
    for raw_token in tokens:
        token = normalize_text(raw_token)
        if not token:
            raise UserInputError("Metric names cannot be blank")
        display = ALIAS_LOOKUP.get(normalize_header(token), token)
        display_key = normalize_header(display)
        if display_key in seen_display:
            raise UserInputError(f"Metric is selected more than once: {token}")
        seen_display.add(display_key)
        base_slug = slugify_metric(display)
        used_slugs[base_slug] += 1
        slug = base_slug if used_slugs[base_slug] == 1 else f"{base_slug}_{used_slugs[base_slug]}"
        metric_name, unit = metric_name_and_unit(display)
        specs.append(
            MetricSpec(
                requested=token,
                display_name=display,
                metric_name=metric_name,
                unit=unit,
                slug=slug,
            )
        )
    return specs


def resolve_metric_header(table: CsvTable, metric: MetricSpec) -> str:
    target_norm = normalize_header(metric.display_name)
    matches = [internal for internal, original in header_pairs(table) if normalize_header(original) == target_norm]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise UserInputError(
            f"Metric {metric.requested!r} ({metric.display_name}) not found in {table.path}; "
            "run --list-metrics to inspect available columns"
        )
    raise UserInputError(f"Metric {metric.display_name!r} is duplicated in {table.path}")


def load_json(path: Path) -> dict[str, Any]:
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise UserInputError(f"Cannot read JSON config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise UserInputError("Config root must be a JSON object")
    return data


def resolve_path(base_dir: Path, value: Any, *, field: str) -> Path:
    text = normalize_text(value)
    if not text:
        raise UserInputError(f"Config field {field} cannot be blank")
    path = Path(text).expanduser()
    return (path if path.is_absolute() else base_dir / path).resolve()


def read_registry(table: CsvTable, config: dict[str, Any]) -> list[Animal]:
    columns = {
        "cage": find_registry_header(table, "cage", required=True),
        "tag": find_registry_header(table, "tag", required=True),
        "animal_id": find_registry_header(table, "animal_id", required=False),
        "group": find_registry_header(table, "group", required=True),
        "notes": find_registry_header(table, "notes", required=False),
        "death_date": find_registry_header(table, "death_date", required=False),
    }
    mode = normalize_text(config.get("animal_id_mode", "cage_tag_concat"))
    if mode not in {"cage_tag_concat", "registry"}:
        raise UserInputError("animal_id_mode must be 'cage_tag_concat' or 'registry'")
    max_tag = config.get("max_tag_number", 5)
    if max_tag is not None:
        try:
            max_tag = int(max_tag)
        except (TypeError, ValueError) as exc:
            raise UserInputError("max_tag_number must be an integer or null") from exc
        if max_tag < 0:
            raise UserInputError("max_tag_number cannot be negative")

    animals: list[Animal] = []
    ids: dict[str, int] = {}
    cage_tags: dict[tuple[str, str], int] = {}
    for row in table.rows:
        if not any(normalize_text(value) for value in row.values.values()):
            continue
        cage = normalize_identifier(row.values[columns["cage"]])
        tag = normalize_identifier(row.values[columns["tag"]])
        group = normalize_text(row.values[columns["group"]])
        provided_id = (
            normalize_identifier(row.values[columns["animal_id"]]) if columns["animal_id"] else ""
        )
        notes = normalize_text(row.values[columns["notes"]]) if columns["notes"] else ""
        death_raw = row.values[columns["death_date"]] if columns["death_date"] else ""

        if not cage or not tag:
            raise UserInputError(f"Registry row {row.record_no}: cage and tag are required")
        if not group:
            raise UserInputError(f"Registry row {row.record_no}: group is required")
        if max_tag is not None:
            if not re.fullmatch(r"\d+", tag) or not (0 <= int(tag) <= max_tag):
                raise UserInputError(
                    f"Registry row {row.record_no}: tag {tag!r} is outside 0-{max_tag}; "
                    "change max_tag_number only if the experimental ID rule is different"
                )
        derived_id = f"{cage}{tag}"
        if mode == "cage_tag_concat":
            if provided_id and provided_id != derived_id:
                raise UserInputError(
                    f"Registry row {row.record_no}: animal_id {provided_id!r} does not equal "
                    f"cage+tag {derived_id!r}"
                )
            animal_id = derived_id
        else:
            if not provided_id:
                raise UserInputError(
                    f"Registry row {row.record_no}: animal_id is required when animal_id_mode=registry"
                )
            animal_id = provided_id

        death_date = parse_date(death_raw, context=f"registry row {row.record_no} death_date")
        if animal_id in ids:
            raise UserInputError(
                f"Duplicate animal_id {animal_id!r} in registry rows {ids[animal_id]} and {row.record_no}"
            )
        cage_tag = (cage, tag)
        if cage_tag in cage_tags:
            raise UserInputError(
                f"Duplicate cage/tag {cage}/{tag} in registry rows "
                f"{cage_tags[cage_tag]} and {row.record_no}"
            )
        ids[animal_id] = row.record_no
        cage_tags[cage_tag] = row.record_no
        animals.append(
            Animal(
                registry_row=row.record_no,
                cage=cage,
                tag=tag,
                animal_id=animal_id,
                group=group,
                notes=notes,
                death_date=death_date,
            )
        )
    if not animals:
        raise UserInputError("Animal registry contains no data rows")
    return animals


def add_issue(
    issues: list[dict[str, str]],
    severity: str,
    code: str,
    details: str,
    *,
    animal_id: str = "",
    timepoint: str = "",
    metric: str = "",
    source_file: str = "",
    source_row: str = "",
) -> None:
    issues.append(
        {
            "severity": severity,
            "code": code,
            "animal_id": animal_id,
            "timepoint": timepoint,
            "metric": metric,
            "source_file": source_file,
            "source_row": source_row,
            "details": details,
        }
    )


def dilution_metric_names(value: Any, metrics: list[MetricSpec], *, context: str) -> tuple[str, ...]:
    text = normalize_text(value)
    all_names = tuple(metric.display_name for metric in metrics)
    if not text or normalize_key(text) in {"all", "全部", "所有"} or text == "*":
        return all_names

    tokens = [normalize_text(token) for token in re.split(r"[,;，；]+", text)]
    tokens = [token for token in tokens if token]
    if not tokens:
        return all_names
    if any(normalize_key(token) in {"all", "全部", "所有"} or token == "*" for token in tokens):
        raise UserInputError(f"{context}: use ALL alone or list individual metrics")

    lookup: dict[str, str] = {}
    for metric in metrics:
        aliases = {
            metric.requested,
            metric.display_name,
            metric.metric_name,
            CANONICAL_TO_ALIAS.get(normalize_header(metric.display_name), ""),
        }
        for alias in aliases:
            if alias:
                lookup[normalize_header(alias)] = metric.display_name

    selected: list[str] = []
    for token in tokens:
        display_name = lookup.get(normalize_header(token))
        if display_name is None:
            raise UserInputError(
                f"{context}: dilution metric {token!r} is not among the selected metrics"
            )
        if display_name not in selected:
            selected.append(display_name)
    return tuple(selected)


def load_dilutions(
    config: dict[str, Any],
    base_dir: Path,
    animals: list[Animal],
    timepoints: list[TimepointData],
    metrics: list[MetricSpec],
    issues: list[dict[str, str]],
) -> tuple[dict[tuple[str, str], DilutionSpec], CsvTable | None]:
    configured_path = normalize_text(config.get("sample_dilutions", ""))
    if not configured_path:
        return {}, None

    dilution_path = resolve_path(base_dir, configured_path, field="sample_dilutions")
    table = read_csv_table(dilution_path)
    columns = {
        "animal_id": find_dilution_header(table, "animal_id", required=True),
        "timepoint": find_dilution_header(table, "timepoint", required=True),
        "dilution_factor": find_dilution_header(table, "dilution_factor", required=True),
        "metrics": find_dilution_header(table, "metrics", required=False),
        "notes": find_dilution_header(table, "notes", required=False),
    }
    expected_ids = {animal.animal_id for animal in animals}
    expected_timepoints = {timepoint.label for timepoint in timepoints}
    dilutions: dict[tuple[str, str], DilutionSpec] = {}

    for row in table.rows:
        animal_id = normalize_identifier(row.values[columns["animal_id"]])
        timepoint = normalize_text(row.values[columns["timepoint"]])
        factor_raw = normalize_text(row.values[columns["dilution_factor"]])
        metric_raw = row.values[columns["metrics"]] if columns["metrics"] else ""
        notes = normalize_text(row.values[columns["notes"]]) if columns["notes"] else ""
        context = f"Dilution row {row.record_no}"

        if not animal_id or not timepoint or not factor_raw:
            raise UserInputError(
                f"{context}: animal_id, timepoint and dilution_factor are required"
            )
        if animal_id not in expected_ids:
            raise UserInputError(f"{context}: animal_id {animal_id!r} is not in the registry")
        if timepoint not in expected_timepoints:
            raise UserInputError(
                f"{context}: timepoint {timepoint!r} is not in the configured timepoints"
            )
        factor = parse_decimal(factor_raw)
        if factor is None or factor < 1:
            raise UserInputError(
                f"{context}: dilution_factor must be the numeric multiplier to apply and must be >= 1"
            )
        key = (animal_id, timepoint)
        if key in dilutions:
            raise UserInputError(
                f"Duplicate dilution record for animal {animal_id!r} at timepoint {timepoint!r}"
            )
        metric_names = dilution_metric_names(metric_raw, metrics, context=context)
        spec = DilutionSpec(
            source_row=row.record_no,
            animal_id=animal_id,
            timepoint=timepoint,
            dilution_factor=factor,
            metric_display_names=metric_names,
            notes=notes,
        )
        dilutions[key] = spec
        add_issue(
            issues,
            "INFO",
            "DILUTION_CONFIGURED",
            "Analyzer values will be multiplied by "
            f"{decimal_to_string(factor)} before D0 normalization; metrics: "
            + ";".join(metric_names),
            animal_id=animal_id,
            timepoint=timepoint,
            source_file=str(dilution_path),
            source_row=str(row.record_no),
        )
    return dilutions, table


def annotate_source_values_with_dilutions(
    source_values: list[dict[str, Any]],
    dilutions: dict[tuple[str, str], DilutionSpec],
) -> None:
    for row in source_values:
        spec = dilutions.get((normalize_identifier(row["sample_id"]), row["timepoint"]))
        applies = spec is not None and row["metric"] in spec.metric_display_names
        factor = spec.dilution_factor if applies else Decimal(1)
        raw_numeric = parse_decimal(row["raw_value"])
        row["dilution_factor"] = factor
        row["corrected_value"] = raw_numeric * factor if raw_numeric is not None else None
        row["dilution_notes"] = spec.notes if applies else ""


def normalized_dilution_rows(dilutions: dict[tuple[str, str], DilutionSpec]) -> list[dict[str, Any]]:
    return [
        {
            "source_row": spec.source_row,
            "animal_id": spec.animal_id,
            "timepoint": spec.timepoint,
            "dilution_factor": spec.dilution_factor,
            "metrics": ";".join(spec.metric_display_names),
            "notes": spec.notes,
        }
        for spec in dilutions.values()
    ]


def load_timepoints(
    config: dict[str, Any],
    base_dir: Path,
    animals: list[Animal],
    metrics: list[MetricSpec],
    issues: list[dict[str, str]],
) -> tuple[list[TimepointData], list[CsvTable], list[dict[str, Any]]]:
    raw_timepoints = config.get("timepoints")
    if not isinstance(raw_timepoints, list) or not raw_timepoints:
        raise UserInputError("Config timepoints must be a non-empty list")
    sample_id_column = normalize_text(config.get("sample_id_column", "样本编号"))
    date_column = normalize_text(config.get("date_column", "日期"))
    excluded_ids = {
        normalize_identifier(value).casefold()
        for value in config.get("excluded_sample_ids", ["Background"])
    }
    expected_ids = {animal.animal_id for animal in animals}
    seen_labels: set[str] = set()
    seen_sources: set[Path] = set()
    all_tables: list[CsvTable] = []
    source_values: list[dict[str, Any]] = []
    output: list[TimepointData] = []

    for tp_index, item in enumerate(raw_timepoints, start=1):
        if not isinstance(item, dict):
            raise UserInputError(f"timepoints[{tp_index}] must be an object")
        label = normalize_text(item.get("label"))
        if not label:
            raise UserInputError(f"timepoints[{tp_index}].label cannot be blank")
        if label in seen_labels:
            raise UserInputError(f"Duplicate timepoint label: {label}")
        seen_labels.add(label)

        raw_sources = item.get("sources")
        if raw_sources is None and item.get("source") is not None:
            raw_sources = [item.get("source")]
        if not isinstance(raw_sources, list) or not raw_sources:
            raise UserInputError(f"Timepoint {label} must define source or sources")
        sources = [
            resolve_path(base_dir, value, field=f"timepoints[{tp_index}].sources")
            for value in raw_sources
        ]
        for source in sources:
            if source in seen_sources:
                raise UserInputError(f"Raw source is assigned more than once: {source}")
            seen_sources.add(source)

        configured_date = parse_date(
            item.get("collection_date", ""), context=f"timepoint {label} collection_date"
        )
        rows_by_id: dict[str, list[SourceMeasurement]] = defaultdict(list)
        parsed_source_dates: set[date] = set()

        for source in sources:
            table = read_csv_table(source)
            all_tables.append(table)
            sample_header = find_exact_header(table, sample_id_column)
            date_header = find_exact_header(table, date_column) if date_column else None
            metric_headers = {metric.display_name: resolve_metric_header(table, metric) for metric in metrics}

            for row in table.rows:
                sample_id = normalize_identifier(row.values[sample_header])
                if not sample_id:
                    if any(normalize_text(row.values[header]) for header in metric_headers.values()):
                        add_issue(
                            issues,
                            "WARNING",
                            "BLANK_SAMPLE_ID",
                            "Row has selected metric data but no sample ID",
                            timepoint=label,
                            source_file=str(source),
                            source_row=str(row.record_no),
                        )
                    continue
                if sample_id.casefold() in excluded_ids:
                    continue

                source_date_raw = normalize_text(row.values[date_header]) if date_header else ""
                source_date = None
                if source_date_raw:
                    try:
                        source_date = parse_date(
                            source_date_raw,
                            context=f"{source} record {row.record_no} {date_column}",
                        )
                    except UserInputError:
                        add_issue(
                            issues,
                            "WARNING",
                            "UNPARSEABLE_SOURCE_DATE",
                            f"Could not parse source date {source_date_raw!r}",
                            animal_id=sample_id,
                            timepoint=label,
                            source_file=str(source),
                            source_row=str(row.record_no),
                        )
                if source_date:
                    parsed_source_dates.add(source_date)

                raw_values = {
                    metric.display_name: normalize_text(row.values[metric_headers[metric.display_name]])
                    for metric in metrics
                }
                measurement = SourceMeasurement(
                    sample_id=sample_id,
                    source_file=str(source),
                    source_row=row.record_no,
                    source_date=source_date,
                    source_date_raw=source_date_raw,
                    raw_values=raw_values,
                )
                rows_by_id[sample_id].append(measurement)
                for metric in metrics:
                    source_values.append(
                        {
                            "timepoint": label,
                            "collection_date": configured_date,
                            "sample_id": sample_id,
                            "is_expected_animal": "YES" if sample_id in expected_ids else "NO",
                            "metric": metric.display_name,
                            "raw_value": raw_values[metric.display_name],
                            "source_date": source_date,
                            "source_date_raw": source_date_raw,
                            "source_file": str(source),
                            "source_row": row.record_no,
                        }
                    )

        collection_date = configured_date
        if collection_date is None:
            if len(parsed_source_dates) == 1:
                collection_date = next(iter(parsed_source_dates))
            elif not parsed_source_dates:
                raise UserInputError(
                    f"Timepoint {label} has no collection_date and no parseable source dates"
                )
            else:
                values = ", ".join(sorted(day.isoformat() for day in parsed_source_dates))
                raise UserInputError(
                    f"Timepoint {label} has no collection_date and contains multiple source dates: {values}"
                )

        if parsed_source_dates and parsed_source_dates != {collection_date}:
            values = ", ".join(sorted(day.isoformat() for day in parsed_source_dates))
            add_issue(
                issues,
                "WARNING",
                "SOURCE_DATE_MISMATCH",
                f"Configured collection date is {collection_date}; source dates are {values}",
                timepoint=label,
                source_file="; ".join(str(path) for path in sources),
            )

        for sample_id, matches in rows_by_id.items():
            if sample_id not in expected_ids:
                add_issue(
                    issues,
                    "INFO",
                    "UNEXPECTED_SAMPLE_ID",
                    f"Sample is present in raw data but absent from the initial registry ({len(matches)} row(s))",
                    animal_id=sample_id,
                    timepoint=label,
                    source_file="; ".join(sorted({match.source_file for match in matches})),
                    source_row="; ".join(str(match.source_row) for match in matches),
                )

        for row in source_values:
            if row["timepoint"] == label and not row["collection_date"]:
                row["collection_date"] = collection_date

        output.append(
            TimepointData(
                label=label,
                day=item.get("day", ""),
                collection_date=collection_date,
                sources=sources,
                rows_by_id=dict(rows_by_id),
            )
        )
    return output, all_tables, source_values


def animal_is_dead(animal: Animal, collection_date: date, inclusive: bool) -> bool:
    if animal.death_date is None:
        return False
    return collection_date >= animal.death_date if inclusive else collection_date > animal.death_date


def append_flag(record: dict[str, Any], flag: str) -> None:
    flags = [part for part in str(record.get("qc_flags", "")).split(";") if part]
    if flag not in flags:
        flags.append(flag)
    record["qc_flags"] = ";".join(flags)


def build_results(
    animals: list[Animal],
    timepoints: list[TimepointData],
    metrics: list[MetricSpec],
    baseline_label: str,
    percent_mode: str,
    death_date_inclusive: bool,
    dilutions: dict[tuple[str, str], DilutionSpec],
    issues: list[dict[str, str]],
) -> tuple[list[dict[str, Any]], str]:
    if baseline_label not in {tp.label for tp in timepoints}:
        raise UserInputError(f"baseline_timepoint {baseline_label!r} is not in timepoints")
    if percent_mode == "percent_of_baseline":
        pct_field = "pct_D0"
    elif percent_mode == "percent_change":
        pct_field = "delta_pct_D0"
    else:
        raise UserInputError(
            "percent_mode must be 'percent_of_baseline' or 'percent_change'"
        )

    results: list[dict[str, Any]] = []
    for animal in animals:
        for tp in timepoints:
            matches = tp.rows_by_id.get(animal.animal_id, [])
            dilution = dilutions.get((animal.animal_id, tp.label))
            is_dead = animal_is_dead(animal, tp.collection_date, death_date_inclusive)
            shared_status = ""
            shared_flags: list[str] = []
            if not matches:
                if is_dead:
                    shared_status = "DEAD"
                else:
                    shared_status = "MISSING_SAMPLE"
                    shared_flags.append("MISSING_SAMPLE")
                    add_issue(
                        issues,
                        "WARNING",
                        "MISSING_SAMPLE",
                        "Animal is expected to be alive but has no raw row at this timepoint",
                        animal_id=animal.animal_id,
                        timepoint=tp.label,
                    )
            elif len(matches) > 1:
                shared_status = "DUPLICATE_SAMPLE"
                shared_flags.append("DUPLICATE_SAMPLE")
                add_issue(
                    issues,
                    "ERROR",
                    "DUPLICATE_SAMPLE",
                    f"Found {len(matches)} candidate raw rows; no row was selected",
                    animal_id=animal.animal_id,
                    timepoint=tp.label,
                    source_file="; ".join(sorted({match.source_file for match in matches})),
                    source_row="; ".join(str(match.source_row) for match in matches),
                )
            elif is_dead:
                shared_status = "POST_DEATH_DATA"
                shared_flags.append("POST_DEATH_DATA")
                match = matches[0]
                add_issue(
                    issues,
                    "ERROR",
                    "POST_DEATH_DATA",
                    "Raw measurement exists on or after the recorded death boundary",
                    animal_id=animal.animal_id,
                    timepoint=tp.label,
                    source_file=match.source_file,
                    source_row=str(match.source_row),
                )

            for metric in metrics:
                raw_value = ""
                value: Decimal | None = None
                parsed_value: Decimal | None = None
                dilution_applies = (
                    dilution is not None
                    and metric.display_name in dilution.metric_display_names
                )
                dilution_factor = (
                    dilution.dilution_factor if dilution_applies else Decimal(1)
                )
                dilution_notes = dilution.notes if dilution_applies else ""
                status = shared_status
                flags = list(shared_flags)
                source_file = ""
                source_row = ""
                if len(matches) == 1:
                    match = matches[0]
                    raw_value = match.raw_values[metric.display_name]
                    source_file = match.source_file
                    source_row = str(match.source_row)
                    parsed_value = parse_decimal(raw_value)
                    if raw_value and parsed_value is None:
                        flags.append("NON_NUMERIC")
                        add_issue(
                            issues,
                            "ERROR",
                            "NON_NUMERIC",
                            f"Cannot parse raw value {raw_value!r} as a finite number",
                            animal_id=animal.animal_id,
                            timepoint=tp.label,
                            metric=metric.display_name,
                            source_file=source_file,
                            source_row=source_row,
                        )
                    elif not raw_value:
                        flags.append("MISSING_METRIC")
                        add_issue(
                            issues,
                            "WARNING",
                            "MISSING_METRIC",
                            "Sample row exists but selected metric is blank",
                            animal_id=animal.animal_id,
                            timepoint=tp.label,
                            metric=metric.display_name,
                            source_file=source_file,
                            source_row=source_row,
                        )
                    else:
                        value = parsed_value * dilution_factor

                    if dilution_factor != 1:
                        flags.append(
                            "DILUTION_APPLIED"
                            if parsed_value is not None
                            else "DILUTION_NOT_APPLIED"
                        )

                    if not shared_status:
                        if not raw_value:
                            status = "MISSING_METRIC"
                        elif parsed_value is None:
                            status = "NON_NUMERIC"
                        else:
                            status = "OBSERVED"
                elif len(matches) > 1:
                    raw_value = " | ".join(
                        match.raw_values[metric.display_name] for match in matches
                    )
                    source_file = "; ".join(sorted({match.source_file for match in matches}))
                    source_row = "; ".join(str(match.source_row) for match in matches)

                results.append(
                    {
                        "registry_row": animal.registry_row,
                        "cage": animal.cage,
                        "tag": animal.tag,
                        "animal_id": animal.animal_id,
                        "group": animal.group,
                        "notes": animal.notes,
                        "death_date": animal.death_date,
                        "timepoint": tp.label,
                        "day": tp.day,
                        "collection_date": tp.collection_date,
                        "metric": metric.display_name,
                        "metric_name": metric.metric_name,
                        "unit": metric.unit,
                        "raw_value": raw_value,
                        "dilution_factor": dilution_factor,
                        "corrected_value": value,
                        "dilution_notes": dilution_notes,
                        "value": value,
                        "baseline_value": None,
                        pct_field: None,
                        "status": status,
                        "qc_flags": ";".join(dict.fromkeys(flags)),
                        "source_file": source_file,
                        "source_row": source_row,
                    }
                )

    for dilution in dilutions.values():
        if dilution.dilution_factor == 1:
            continue
        related = [
            record
            for record in results
            if record["animal_id"] == dilution.animal_id
            and record["timepoint"] == dilution.timepoint
            and record["metric"] in dilution.metric_display_names
        ]
        if not any("DILUTION_APPLIED" in record["qc_flags"].split(";") for record in related):
            add_issue(
                issues,
                "WARNING",
                "DILUTION_NOT_APPLIED",
                "A dilution was configured, but no targeted metric had a unique numeric raw value",
                animal_id=dilution.animal_id,
                timepoint=dilution.timepoint,
                metric=";".join(dilution.metric_display_names),
            )

    by_key = {
        (record["animal_id"], record["metric"], record["timepoint"]): record
        for record in results
    }
    for animal in animals:
        for metric in metrics:
            baseline = by_key[(animal.animal_id, metric.display_name, baseline_label)]
            baseline_value = baseline["value"] if baseline["status"] == "OBSERVED" else None
            related = [
                record
                for record in results
                if record["animal_id"] == animal.animal_id
                and record["metric"] == metric.display_name
            ]
            if baseline_value is None:
                add_issue(
                    issues,
                    "ERROR",
                    "BASELINE_UNAVAILABLE",
                    f"Baseline status is {baseline['status']}; D0 normalization is unavailable",
                    animal_id=animal.animal_id,
                    timepoint=baseline_label,
                    metric=metric.display_name,
                )
                for record in related:
                    append_flag(record, "BASELINE_UNAVAILABLE")
                continue
            for record in related:
                record["baseline_value"] = baseline_value
            if baseline_value == 0:
                add_issue(
                    issues,
                    "WARNING",
                    "BASELINE_ZERO",
                    "Baseline value is zero; division-based normalization is blank",
                    animal_id=animal.animal_id,
                    timepoint=baseline_label,
                    metric=metric.display_name,
                )
                for record in related:
                    append_flag(record, "BASELINE_ZERO")
                continue
            for record in related:
                if record["status"] != "OBSERVED" or record["value"] is None:
                    continue
                if percent_mode == "percent_of_baseline":
                    percentage = record["value"] / baseline_value * Decimal(100)
                else:
                    percentage = (record["value"] - baseline_value) / baseline_value * Decimal(100)
                record[pct_field] = percentage.quantize(PCT_QUANTUM, rounding=ROUND_HALF_UP)
    return results, pct_field


def normalized_registry_rows(animals: list[Animal]) -> list[dict[str, Any]]:
    return [
        {
            "registry_row": animal.registry_row,
            "cage": animal.cage,
            "tag": animal.tag,
            "animal_id": animal.animal_id,
            "group": animal.group,
            "notes": animal.notes,
            "death_date": animal.death_date,
        }
        for animal in animals
    ]


def build_group_map(
    animals: list[Animal], group_order: list[str] | None = None
) -> "OrderedDict[str, list[Animal]]":
    """Return animals grouped in registry order or an explicit complete order."""
    groups: "OrderedDict[str, list[Animal]]" = OrderedDict()
    for animal in animals:
        groups.setdefault(animal.group, []).append(animal)

    if group_order is None or group_order == []:
        return groups
    if not isinstance(group_order, list):
        raise UserInputError("group_order must be a JSON array of group names")
    normalized_order = [normalize_text(group) for group in group_order]
    if any(not group for group in normalized_order):
        raise UserInputError("group_order cannot contain blank group names")
    if len(normalized_order) != len(set(normalized_order)):
        raise UserInputError("group_order must list each group exactly once")
    unknown = [group for group in normalized_order if group not in groups]
    missing = [group for group in groups if group not in normalized_order]
    if unknown or missing:
        details = []
        if unknown:
            details.append("unknown=" + ",".join(unknown))
        if missing:
            details.append("missing=" + ",".join(missing))
        raise UserInputError(
            "group_order must be a complete permutation of registry groups ("
            + "; ".join(details)
            + ")"
        )
    return OrderedDict((group, groups[group]) for group in normalized_order)


def parse_group_order(config: dict[str, Any], animals: list[Animal]) -> list[str]:
    """Validate the optional group_order setting and return the effective order."""
    requested = config.get("group_order")
    if requested in (None, []):
        return list(build_group_map(animals))
    if not isinstance(requested, list):
        raise UserInputError("group_order must be a JSON array of group names")
    return list(build_group_map(animals, requested))


def parse_group_order_option(value: str) -> list[str]:
    """Parse a comma-separated CLI group order before registry validation."""
    groups = [normalize_text(item) for item in value.split(",")]
    if not groups or any(not group for group in groups):
        raise argparse.ArgumentTypeError(
            "group order must be a comma-separated list without blank names"
        )
    if len(groups) != len(set(groups)):
        raise argparse.ArgumentTypeError("group order cannot contain duplicate names")
    return groups


def build_group_summary(
    animals: list[Animal], group_order: list[str] | None = None
) -> list[dict[str, Any]]:
    groups = build_group_map(animals, group_order)
    return [
        {
            "group": group,
            "initial_n": len(members),
            "deaths_recorded_n": sum(member.death_date is not None for member in members),
            "animal_ids": ";".join(member.animal_id for member in members),
            "cages": ";".join(dict.fromkeys(member.cage for member in members)),
        }
        for group, members in groups.items()
    ]


def build_wide_rows(
    animals: list[Animal],
    timepoints: list[TimepointData],
    metric: MetricSpec,
    results: list[dict[str, Any]],
    baseline_label: str,
    pct_field: str,
    *,
    include_status: bool = True,
) -> tuple[list[str], list[dict[str, Any]]]:
    fixed = ["cage", "tag", "animal_id", "group", "notes", "death_date"]
    columns = list(fixed)
    prefix_by_timepoint: dict[str, str] = {}
    for tp in timepoints:
        prefix = f"{tp.label}_{tp.collection_date.isoformat()}"
        prefix_by_timepoint[tp.label] = prefix
        columns.append(f"{prefix}_value")
        if tp.label != baseline_label:
            columns.append(f"{prefix}_{pct_field}")
        if include_status:
            columns.append(f"{prefix}_status")

    result_index = {
        (record["animal_id"], record["timepoint"]): record
        for record in results
        if record["metric"] == metric.display_name
    }
    rows: list[dict[str, Any]] = []
    for animal in animals:
        row: dict[str, Any] = {
            "cage": animal.cage,
            "tag": animal.tag,
            "animal_id": animal.animal_id,
            "group": animal.group,
            "notes": animal.notes,
            "death_date": animal.death_date,
        }
        for tp in timepoints:
            record = result_index[(animal.animal_id, tp.label)]
            prefix = prefix_by_timepoint[tp.label]
            row[f"{prefix}_value"] = record["value"]
            if tp.label != baseline_label:
                row[f"{prefix}_{pct_field}"] = record[pct_field]
            if include_status:
                row[f"{prefix}_status"] = record["status"]
        rows.append(row)
    return columns, rows


def graphpad_x_value(timepoint: TimepointData, baseline_date: date) -> str:
    """Return a numeric X value suitable for a Prism XY table."""
    configured_day = parse_decimal(timepoint.day)
    if configured_day is not None:
        return decimal_to_string(configured_day)
    return str((timepoint.collection_date - baseline_date).days)


def build_graphpad_rows(
    animals: list[Animal],
    timepoints: list[TimepointData],
    metric: MetricSpec,
    results: list[dict[str, Any]],
    baseline_label: str,
    pct_field: str,
    group_order: list[str] | None = None,
) -> list[list[Any]]:
    """Build a Prism-ready XY table with groups as data sets and animals as replicates."""
    groups = build_group_map(animals, group_order)
    max_replicates = max(len(members) for members in groups.values())

    group_header: list[Any] = ["Time (days)"]
    animal_header: list[Any] = ["X"]
    for group, members in groups.items():
        group_header.extend([group] + [""] * (max_replicates - 1))
        animal_header.extend(
            [member.animal_id for member in members]
            + [""] * (max_replicates - len(members))
        )

    result_index = {
        (record["animal_id"], record["timepoint"]): record
        for record in results
        if record["metric"] == metric.display_name
    }
    baseline_date = next(
        timepoint.collection_date
        for timepoint in timepoints
        if timepoint.label == baseline_label
    )
    rows: list[list[Any]] = [group_header, animal_header]
    for timepoint in timepoints:
        row: list[Any] = [graphpad_x_value(timepoint, baseline_date)]
        for members in groups.values():
            for animal in members:
                record = result_index[(animal.animal_id, timepoint.label)]
                row.append(record[pct_field])
            row.extend([""] * (max_replicates - len(members)))
        rows.append(row)
    return rows


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {key: format_csv_field(key, row.get(key, "")) for key in fieldnames}
            )


def write_graphpad_tsv(path: Path, rows: list[list[Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        for row_index, row in enumerate(rows):
            formatted: list[Any] = []
            for column_index, value in enumerate(row):
                if row_index >= 2 and column_index >= 1 and isinstance(value, Decimal):
                    formatted.append(format(value, ".2f"))
                else:
                    formatted.append(format_csv_value(value))
            writer.writerow(formatted)


def table_manifest(table: CsvTable, role: str) -> dict[str, Any]:
    return {
        "role": role,
        "path": str(table.path),
        "sha256": table.sha256,
        "encoding": table.encoding,
        "delimiter": "TAB" if table.delimiter == "\t" else table.delimiter,
        "records": len(table.rows),
        "columns": len(table.headers),
    }


def output_targets(output_dir: Path, metrics: list[MetricSpec]) -> list[Path]:
    names = [
        "animal_registry_normalized.csv",
        "sample_dilutions_normalized.csv",
        "group_summary.csv",
        "source_values.csv",
        "results_long.csv",
        "qc_issues.csv",
        "run_manifest.json",
    ] + [
        name
        for metric in metrics
        for name in (
            f"{metric.slug}_wide.csv",
            f"{metric.slug}_copy_ready.csv",
            f"graphpad_{metric.slug}.tsv",
        )
    ]
    return [output_dir / name for name in names]


def check_output_collisions(output_dir: Path, metrics: list[MetricSpec], overwrite: bool) -> None:
    collisions = [path for path in output_targets(output_dir, metrics) if path.exists()]
    if collisions and not overwrite:
        raise UserInputError(
            "Refusing to overwrite existing output(s); use a new directory or --overwrite: "
            + ", ".join(str(path) for path in collisions)
        )


def run_analysis(
    config_path: Path,
    output_dir: Path,
    *,
    overwrite: bool,
    group_order_override: list[str] | None = None,
) -> tuple[dict[str, Any], int]:
    config_path = config_path.expanduser().resolve()
    config = load_json(config_path)
    base_dir = config_path.parent
    registry_path = resolve_path(base_dir, config.get("animal_registry"), field="animal_registry")
    registry_table = read_csv_table(registry_path)
    animals = read_registry(registry_table, config)
    if group_order_override is None:
        group_order = parse_group_order(config, animals)
        group_order_source = (
            "config" if config.get("group_order") not in (None, []) else "registry"
        )
    else:
        group_order = list(build_group_map(animals, group_order_override))
        group_order_source = "command_line"
    metrics = build_metric_specs(config.get("metrics", []))
    issues: list[dict[str, str]] = []
    timepoints, raw_tables, source_values = load_timepoints(
        config, base_dir, animals, metrics, issues
    )
    dilutions, dilution_table = load_dilutions(
        config, base_dir, animals, timepoints, metrics, issues
    )
    annotate_source_values_with_dilutions(source_values, dilutions)
    baseline_label = normalize_text(config.get("baseline_timepoint", "D0"))
    percent_mode = normalize_text(config.get("percent_mode", "percent_of_baseline"))
    death_date_inclusive = bool(config.get("death_date_inclusive", True))
    results, pct_field = build_results(
        animals,
        timepoints,
        metrics,
        baseline_label,
        percent_mode,
        death_date_inclusive,
        dilutions,
        issues,
    )

    output_dir = output_dir.expanduser().resolve()
    check_output_collisions(output_dir, metrics, overwrite)
    output_dir.mkdir(parents=True, exist_ok=True)

    registry_rows = normalized_registry_rows(animals)
    write_csv(
        output_dir / "animal_registry_normalized.csv",
        ["registry_row", "cage", "tag", "animal_id", "group", "notes", "death_date"],
        registry_rows,
    )
    write_csv(
        output_dir / "sample_dilutions_normalized.csv",
        ["source_row", "animal_id", "timepoint", "dilution_factor", "metrics", "notes"],
        normalized_dilution_rows(dilutions),
    )
    group_rows = build_group_summary(animals, group_order)
    write_csv(
        output_dir / "group_summary.csv",
        ["group", "initial_n", "deaths_recorded_n", "animal_ids", "cages"],
        group_rows,
    )
    write_csv(
        output_dir / "source_values.csv",
        [
            "timepoint",
            "collection_date",
            "sample_id",
            "is_expected_animal",
            "metric",
            "raw_value",
            "dilution_factor",
            "corrected_value",
            "dilution_notes",
            "source_date",
            "source_date_raw",
            "source_file",
            "source_row",
        ],
        source_values,
    )
    long_fields = [
        "registry_row",
        "cage",
        "tag",
        "animal_id",
        "group",
        "notes",
        "death_date",
        "timepoint",
        "day",
        "collection_date",
        "metric",
        "metric_name",
        "unit",
        "raw_value",
        "dilution_factor",
        "corrected_value",
        "dilution_notes",
        "value",
        "baseline_value",
        pct_field,
        "status",
        "qc_flags",
        "source_file",
        "source_row",
    ]
    write_csv(output_dir / "results_long.csv", long_fields, results)
    for metric in metrics:
        columns, wide_rows = build_wide_rows(
            animals, timepoints, metric, results, baseline_label, pct_field
        )
        write_csv(output_dir / f"{metric.slug}_wide.csv", columns, wide_rows)
        copy_columns, copy_rows = build_wide_rows(
            animals,
            timepoints,
            metric,
            results,
            baseline_label,
            pct_field,
            include_status=False,
        )
        write_csv(
            output_dir / f"{metric.slug}_copy_ready.csv",
            copy_columns,
            copy_rows,
        )
        graphpad_rows = build_graphpad_rows(
            animals,
            timepoints,
            metric,
            results,
            baseline_label,
            pct_field,
            group_order,
        )
        write_graphpad_tsv(output_dir / f"graphpad_{metric.slug}.tsv", graphpad_rows)

    severity_rank = {"ERROR": 0, "WARNING": 1, "INFO": 2}
    issues.sort(
        key=lambda issue: (
            severity_rank.get(issue["severity"], 9),
            issue["animal_id"],
            issue["timepoint"],
            issue["metric"],
            issue["code"],
        )
    )
    for index, issue in enumerate(issues, start=1):
        issue["issue_id"] = f"QC-{index:04d}"
    write_csv(
        output_dir / "qc_issues.csv",
        [
            "issue_id",
            "severity",
            "code",
            "animal_id",
            "timepoint",
            "metric",
            "source_file",
            "source_row",
            "details",
        ],
        issues,
    )

    status_counts = Counter(record["status"] for record in results)
    severity_counts = Counter(issue["severity"] for issue in issues)
    input_files = [table_manifest(registry_table, "animal_registry")]
    if dilution_table is not None:
        input_files.append(table_manifest(dilution_table, "sample_dilutions"))
    input_files.extend(table_manifest(table, "raw_measurement") for table in raw_tables)
    manifest = {
        "script": "analyze_hematology.py",
        "script_version": VERSION,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "project_name": normalize_text(config.get("project_name", "")),
        "config_path": str(config_path),
        "baseline_timepoint": baseline_label,
        "percent_mode": percent_mode,
        "formula": (
            "current / baseline * 100"
            if percent_mode == "percent_of_baseline"
            else "(current - baseline) / baseline * 100"
        ),
        "dilution_correction": {
            "records": len(dilutions),
            "formula": "corrected_value = raw_value * dilution_factor",
            "source": str(dilution_table.path) if dilution_table is not None else None,
        },
        "death_date_inclusive": death_date_inclusive,
        "group_order": group_order,
        "group_order_source": group_order_source,
        "metrics": [
            {
                "requested": metric.requested,
                "display_name": metric.display_name,
                "unit": metric.unit,
                "wide_file": f"{metric.slug}_wide.csv",
                "copy_ready_file": f"{metric.slug}_copy_ready.csv",
                "graphpad_file": f"graphpad_{metric.slug}.tsv",
            }
            for metric in metrics
        ],
        "timepoints": [
            {
                "label": tp.label,
                "day": tp.day,
                "collection_date": tp.collection_date.isoformat(),
                "sources": [str(path) for path in tp.sources],
            }
            for tp in timepoints
        ],
        "counts": {
            "animals": len(animals),
            "groups": len({animal.group for animal in animals}),
            "timepoints": len(timepoints),
            "metrics": len(metrics),
            "dilution_records": len(dilutions),
            "long_result_rows": len(results),
            "status": dict(sorted(status_counts.items())),
            "qc_severity": dict(sorted(severity_counts.items())),
        },
        "input_files": input_files,
    }
    with (output_dir / "run_manifest.json").open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(manifest, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return manifest, severity_counts.get("ERROR", 0)


def numeric_fraction(table: CsvTable, internal_header: str) -> float:
    nonempty = [normalize_text(row.values[internal_header]) for row in table.rows]
    nonempty = [value for value in nonempty if value]
    if not nonempty:
        return 0.0
    numeric = sum(parse_decimal(value) is not None for value in nonempty)
    return numeric / len(nonempty)


def list_metrics(paths: list[Path]) -> int:
    tables = [read_csv_table(path.expanduser().resolve()) for path in paths]
    candidates: "OrderedDict[str, dict[str, Any]]" = OrderedDict()
    for table in tables:
        print(
            f"# {table.path} | encoding={table.encoding} | rows={len(table.rows)} | "
            f"columns={len(table.headers)}"
        )
        for internal, original in header_pairs(table):
            if not original or normalize_text(original) in NON_METRIC_HEADERS:
                continue
            canonical_alias = CANONICAL_TO_ALIAS.get(normalize_header(original), "")
            fraction = numeric_fraction(table, internal)
            if not canonical_alias and fraction < 0.8:
                continue
            key = normalize_header(original)
            item = candidates.setdefault(
                key,
                {
                    "alias": canonical_alias,
                    "header": original,
                    "files": set(),
                    "fractions": [],
                },
            )
            item["files"].add(str(table.path))
            item["fractions"].append(fraction)
    print("\nAlias\tExact header\tFiles present\tNumeric fraction")
    for item in candidates.values():
        print(
            f"{item['alias']}\t{item['header']}\t{len(item['files'])}/{len(tables)}\t"
            f"{min(item['fractions']):.2f}"
        )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Match longitudinal hematology CSV rows to an initial animal registry."
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--config", type=Path, help="analysis_config.json")
    mode.add_argument(
        "--list-metrics",
        nargs="+",
        type=Path,
        metavar="CSV",
        help="inspect metric-like columns without writing output",
    )
    parser.add_argument("--output-dir", type=Path, help="output directory for analysis mode")
    parser.add_argument(
        "--group-order",
        type=parse_group_order_option,
        metavar="GROUP1,GROUP2,...",
        help=(
            "left-to-right GraphPad group order for this run; overrides config group_order "
            "and must list every registry group exactly once"
        ),
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="overwrite only this script's known output files",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit with code 3 after writing outputs if QC contains ERROR",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.list_metrics:
            if args.output_dir:
                raise UserInputError("--output-dir is not used with --list-metrics")
            if args.group_order is not None:
                raise UserInputError("--group-order is not used with --list-metrics")
            return list_metrics(args.list_metrics)
        if args.output_dir is None:
            raise UserInputError("--output-dir is required with --config")
        manifest, error_count = run_analysis(
            args.config,
            args.output_dir,
            overwrite=args.overwrite,
            group_order_override=args.group_order,
        )
        counts = manifest["counts"]
        print(f"Output: {args.output_dir.expanduser().resolve()}")
        print(
            f"Animals={counts['animals']} Timepoints={counts['timepoints']} "
            f"Metrics={counts['metrics']} Dilutions={counts['dilution_records']} "
            f"Rows={counts['long_result_rows']}"
        )
        print(f"Status counts: {counts['status']}")
        print(f"QC severity counts: {counts['qc_severity']}")
        print(
            "GraphPad group order: "
            + " | ".join(manifest["group_order"])
            + f" ({manifest['group_order_source']})"
        )
        if args.strict and error_count:
            print(
                f"Strict mode: {error_count} QC ERROR issue(s) require resolution.",
                file=sys.stderr,
            )
            return 3
        return 0
    except UserInputError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
