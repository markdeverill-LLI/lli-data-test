#!/usr/bin/env python3
"""List Parquet fields and report how completely each field is populated.

"With values" means non-null at the top level. Empty strings, zero, False,
NaN, empty lists, and empty objects are values rather than nulls.
Minimum and maximum ignore nulls; unordered and all-null fields report N/A.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, TextIO


def load_pyarrow() -> Any:
    try:
        import pyarrow.dataset as dataset
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "pyarrow is required to read Parquet files. Install it with:\n"
            "  python3 -m pip install pyarrow"
        ) from exc
    return dataset


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Report every field in a Parquet file (or directory), including "
            "its type, populated record count, null count, coverage percentage, "
            "and minimum and maximum non-null values."
        )
    )
    parser.add_argument("path", help="A .parquet file or directory of Parquet files")
    parser.add_argument(
        "--format",
        choices=("table", "csv", "json"),
        default="table",
        help="Output format (default: table)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write the report to this file instead of standard output",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=131_072,
        help="Rows processed at a time (default: 131072)",
    )
    parser.add_argument(
        "--source-column",
        default="source",
        help="Field used to group source statistics (default: source)",
    )
    parser.add_argument(
        "--vessel-id-column",
        default="vesselid",
        help="Field counted uniquely within each source (default: vesselid)",
    )
    parser.add_argument(
        "--onlypositions",
        choices=("Y", "N", "y", "n"),
        default="N",
        help=(
            "Only parse records that are positions, i.e. have non-null values "
            "in both a latitude field (lat/latitude) and a longitude field "
            "(lon/longitude), any casing (default: N)"
        ),
    )
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")
    args.onlypositions = args.onlypositions.upper() == "Y"
    return args


def find_parquet_files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(item for item in path.rglob("*.parquet") if item.is_file())
    raise SystemExit(f"Input path does not exist: {path}")


LATITUDE_CANDIDATES = ("lat", "latitude")
LONGITUDE_CANDIDATES = ("lon", "long", "longitude")


def find_column_index(
    field_indexes: dict[str, int], candidates: tuple[str, ...]
) -> int | None:
    lowered = {name.lower(): index for name, index in field_indexes.items()}
    for candidate in candidates:
        index = lowered.get(candidate)
        if index is not None:
            return index
    return None


def format_extreme(value: Any) -> Any:
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, (Decimal, timedelta)):
        return str(value)
    if isinstance(value, bytes):
        return "0x" + value.hex()
    return value


def calculate_coverage(
    path: Path,
    batch_size: int,
    source_column: str = "source",
    vessel_id_column: str = "vesselid",
    only_positions: bool = False,
) -> dict[str, Any]:
    ds = load_pyarrow()
    import pyarrow as pa
    import pyarrow.compute as pc

    files = find_parquet_files(path)
    if not files:
        raise SystemExit(f"No .parquet files found under: {path}")

    # Passing the explicit file list avoids treating unrelated files in a
    # directory as part of the dataset.
    dataset = ds.dataset([str(file) for file in files], format="parquet")
    fields = list(dataset.schema)
    populated = [0] * len(fields)
    minimums: list[Any] = [None] * len(fields)
    maximums: list[Any] = [None] * len(fields)
    unsupported: set[int] = set()
    total_records = 0
    field_indexes = {field.name: index for index, field in enumerate(fields)}
    source_index = field_indexes.get(source_column)
    vessel_id_index = field_indexes.get(vessel_id_column)
    source_record_counts: dict[Any, int] = defaultdict(int)
    source_vessel_ids: dict[Any, set[Any]] = defaultdict(set)

    latitude_index = find_column_index(field_indexes, LATITUDE_CANDIDATES)
    longitude_index = find_column_index(field_indexes, LONGITUDE_CANDIDATES)
    if only_positions and (latitude_index is None or longitude_index is None):
        raise SystemExit(
            "--onlypositions Y requires latitude and longitude fields "
            "(lat/latitude and lon/long/longitude); none were found in the schema."
        )

    scanner = dataset.scanner(
        columns=[field.name for field in fields], batch_size=batch_size
    )
    for batch in scanner.to_batches():
        if only_positions:
            position_mask = pc.and_(
                pc.is_valid(batch.column(latitude_index)),
                pc.is_valid(batch.column(longitude_index)),
            )
            batch = batch.filter(position_mask)
            if batch.num_rows == 0:
                continue

        total_records += batch.num_rows
        for index, column in enumerate(batch.columns):
            populated[index] += batch.num_rows - column.null_count
            if index in unsupported or column.null_count == batch.num_rows:
                continue
            try:
                extrema = pc.min_max(column).as_py()
            except pa.ArrowNotImplementedError:
                unsupported.add(index)
                continue
            batch_min, batch_max = extrema["min"], extrema["max"]
            if batch_min is not None and (
                minimums[index] is None or batch_min < minimums[index]
            ):
                minimums[index] = batch_min
            if batch_max is not None and (
                maximums[index] is None or batch_max > maximums[index]
            ):
                maximums[index] = batch_max

        if source_index is not None:
            sources = batch.column(source_index).to_pylist()
            vessel_ids = (
                batch.column(vessel_id_index).to_pylist()
                if vessel_id_index is not None
                else [None] * batch.num_rows
            )
            for source, vessel_id in zip(sources, vessel_ids):
                source_record_counts[source] += 1
                if vessel_id is not None:
                    source_vessel_ids[source].add(vessel_id)

    field_results = []
    for index, (field, value_count) in enumerate(zip(fields, populated)):
        coverage = (value_count / total_records * 100.0) if total_records else 0.0
        field_results.append(
            {
                "field": field.name,
                "type": str(field.type),
                "records_with_values": value_count,
                "records_without_values": total_records - value_count,
                "coverage_percent": round(coverage, 6),
                "minimum": (
                    format_extreme(minimums[index]) if index not in unsupported else None
                ),
                "maximum": (
                    format_extreme(maximums[index]) if index not in unsupported else None
                ),
            }
        )

    source_results = []
    for source, record_count in sorted(
        source_record_counts.items(), key=lambda item: (item[0] is None, str(item[0]))
    ):
        source_results.append(
            {
                "source": source,
                "record_count": record_count,
                "record_percent": round(
                    record_count / total_records * 100.0 if total_records else 0.0, 6
                ),
                "unique_vessel_ids": (
                    len(source_vessel_ids[source]) if vessel_id_index is not None else None
                ),
            }
        )

    return {
        "input": str(path),
        "parquet_files": len(files),
        "total_records": total_records,
        "field_count": len(fields),
        "only_positions": only_positions,
        "position_columns": (
            {
                "latitude": fields[latitude_index].name,
                "longitude": fields[longitude_index].name,
            }
            if only_positions
            else None
        ),
        "fields": field_results,
        "source_statistics": {
            "source_column": source_column,
            "vessel_id_column": vessel_id_column,
            "available": source_index is not None,
            "unique_vessel_counts_available": vessel_id_index is not None,
            "groups": source_results,
        },
    }


def print_table(report: dict[str, Any], output: TextIO) -> None:
    rows = report["fields"]
    headers = (
        "Field", "Type", "With values", "Without values", "Coverage", "Minimum", "Maximum"
    )
    formatted = [
        (
            row["field"],
            row["type"],
            f'{row["records_with_values"]:,}',
            f'{row["records_without_values"]:,}',
            f'{row["coverage_percent"]:.2f}%',
            "N/A" if row["minimum"] is None else str(row["minimum"]),
            "N/A" if row["maximum"] is None else str(row["maximum"]),
        )
        for row in rows
    ]
    widths = [
        max(len(headers[index]), *(len(row[index]) for row in formatted))
        for index in range(len(headers))
    ]

    print(f'Input: {report["input"]}', file=output)
    print(f'Parquet files: {report["parquet_files"]:,}', file=output)
    print(f'Total records: {report["total_records"]:,}', file=output)
    print(f'Fields: {report["field_count"]:,}', file=output)
    if report.get("only_positions"):
        position_columns = report["position_columns"]
        print(
            "Filter: only positions "
            f'(non-null {position_columns["latitude"]!r} and '
            f'{position_columns["longitude"]!r})',
            file=output,
        )
    print(file=output)

    def write_row(row: tuple[str, ...]) -> None:
        print("  ".join(value.ljust(widths[i]) for i, value in enumerate(row)), file=output)

    write_row(headers)
    write_row(tuple("-" * width for width in widths))
    for row in formatted:
        write_row(row)

    source_stats = report["source_statistics"]
    print(file=output)
    print("Records and unique vessels by source", file=output)
    if not source_stats["available"]:
        print(f'Source field not found: {source_stats["source_column"]}', file=output)
        return

    source_headers = ("Source", "Records", "% of records", "Unique vessel IDs")
    source_rows = [
        (
            "(null)" if row["source"] is None else str(row["source"]),
            f'{row["record_count"]:,}',
            f'{row["record_percent"]:.2f}%',
            (
                f'{row["unique_vessel_ids"]:,}'
                if row["unique_vessel_ids"] is not None
                else "unavailable"
            ),
        )
        for row in source_stats["groups"]
    ]
    source_widths = [
        max(len(source_headers[index]), *(len(row[index]) for row in source_rows))
        for index in range(len(source_headers))
    ]

    def write_source_row(row: tuple[str, ...]) -> None:
        print(
            "  ".join(value.ljust(source_widths[i]) for i, value in enumerate(row)),
            file=output,
        )

    write_source_row(source_headers)
    write_source_row(tuple("-" * width for width in source_widths))
    for row in source_rows:
        write_source_row(row)


def print_csv(report: dict[str, Any], output: TextIO) -> None:
    fieldnames = (
        "section",
        "field",
        "type",
        "records_with_values",
        "records_without_values",
        "coverage_percent",
        "minimum",
        "maximum",
        "source",
        "record_count",
        "record_percent",
        "unique_vessel_ids",
    )
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in report["fields"]:
        writer.writerow({"section": "field_coverage", **row})
    for row in report["source_statistics"]["groups"]:
        writer.writerow({"section": "source_statistics", **row})


def main() -> int:
    args = parse_args()
    input_path = Path(args.path).expanduser().resolve()
    report = calculate_coverage(
        input_path,
        args.batch_size,
        source_column=args.source_column,
        vessel_id_column=args.vessel_id_column,
        only_positions=args.onlypositions,
    )

    output: TextIO = sys.stdout
    should_close = False
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        output = args.output.open("w", encoding="utf-8", newline="")
        should_close = True

    try:
        if args.format == "json":
            json.dump(report, output, indent=2)
            print(file=output)
        elif args.format == "csv":
            print_csv(report, output)
        else:
            print_table(report, output)
    finally:
        if should_close:
            output.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
