#!/usr/bin/env python3
"""Convert categorized CSV files to Parquet files."""

from __future__ import annotations

import argparse
import fnmatch
import sys
from pathlib import Path
from typing import Any


CATEGORY_MAP = {
    "VO_": "VESSEL_OWNERSHIP",
    "V_": "VESSELS",
    "P_": "PSC",
    "C_": "CASUALTY",
    "VRS_": "VESSELS_RISK_SUMMARY",
}


def load_pandas() -> Any:
    try:
        import pandas
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "pandas and pyarrow are required. Install them with:\n"
            "  python -m pip install pandas pyarrow"
        ) from exc
    return pandas


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Convert matching CSV files to categorized Parquet files beneath "
            "the source folder"
        )
    )
    parser.add_argument(
        "--sourcefolder",
        required=True,
        type=Path,
        help=r"Folder containing CSV files, for example ..\OUTPUT\VESSELS_RISK_SUMMARY",
    )
    parser.add_argument(
        "--filepattern",
        required=True,
        help='Filename pattern to convert, for example "*.CSV"',
    )
    return parser.parse_args()


def find_matching_files(source_folder: Path, pattern: str) -> list[Path]:
    """Find files using case-insensitive matching on all operating systems."""
    folded_pattern = pattern.casefold()
    return sorted(
        (
            path
            for path in source_folder.iterdir()
            if path.is_file()
            and fnmatch.fnmatchcase(path.name.casefold(), folded_pattern)
        ),
        key=lambda path: path.name.casefold(),
    )


def category_for(filename: str) -> str | None:
    folded_filename = filename.casefold()
    for prefix, folder in CATEGORY_MAP.items():
        if folded_filename.startswith(prefix.casefold()):
            return folder
    return None


def main() -> int:
    args = parse_args()
    source_folder = args.sourcefolder.expanduser()

    if not source_folder.is_dir():
        print(
            f"ERROR: Source folder does not exist or is not a directory: "
            f"{source_folder}",
            file=sys.stderr,
        )
        return 1

    csv_files = find_matching_files(source_folder, args.filepattern)
    if not csv_files:
        print(
            f'ERROR: No files matching "{args.filepattern}" were found in '
            f"{source_folder}",
            file=sys.stderr,
        )
        return 1

    print(f"Source folder: {source_folder}")
    print(f'File pattern: "{args.filepattern}"')
    print(f"Files matched: {len(csv_files)}")

    try:
        pandas = load_pandas()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    converted = 0
    skipped = 0
    failed = 0

    for csv_file in csv_files:
        category = category_for(csv_file.name)
        if category is None:
            # skipped += 1
            print(f"SKIPPED: {csv_file.name} - no category prefix match")
            # continue # set to . for current folder, no longer skip.
            category = "." 

        category_folder = source_folder / category
        parquet_path = category_folder / f"{csv_file.stem}.parquet"

        try:
            category_folder.mkdir(parents=True, exist_ok=True)
            print(
                f"Converting {csv_file.name} -> "
                f"{parquet_path.relative_to(source_folder)} ...",
                end=" ",
                flush=True,
            )
            dataframe = pandas.read_csv(
                csv_file,
                encoding="latin-1",
                low_memory=False,
                on_bad_lines="skip",
            )
            dataframe.to_parquet(
                parquet_path,
                engine="pyarrow",
                compression="snappy",
                index=False,
            )
        except Exception as exc:
            failed += 1
            print(f"FAILED: {exc}", file=sys.stderr)
        else:
            converted += 1
            print(f"SUCCESS ({len(dataframe):,} rows)")

    print(
        f"Completed: {converted} converted, {skipped} skipped, {failed} failed"
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
