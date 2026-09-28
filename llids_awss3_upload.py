#!/usr/bin/env python3
"""Upload a single data extract output file to Amazon S3."""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional, Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Upload a single data extract output file to Amazon S3",
        epilog=(
            "Windows: do not end a quoted --path value with a single backslash. "
            'Use --path "Y:\\path\\to\\OUTPUT" instead.'
        ),
    )
    parser.add_argument(
        "--path",
        required=True,
        type=Path,
        help="Folder containing the file to upload",
    )
    parser.add_argument("--bucket", required=True, help="S3 bucket name")
    parser.add_argument(
        "--prefix",
        default="",
        help="Optional S3 target folder/prefix (default: bucket root)",
    )
    parser.add_argument(
        "--ext",
        default=".zip",
        help="File extension to search for (default: .zip)",
    )
    parser.add_argument(
        "--copysubfolders",
        type=str.upper,
        choices=("Y", "N"),
        default="N",
        help=(
            "Upload matching files recursively and preserve subfolder paths "
            "(default: N)"
        ),
    )
    return parser


def normalize_extension(extension: str) -> str:
    return extension if extension.startswith(".") else f".{extension}"


def find_upload_files(
    folder: Path,
    extension: str,
    recursive: bool,
) -> List[Path]:
    normalized_extension = normalize_extension(extension)
    candidates = folder.rglob("*") if recursive else folder.iterdir()
    matches = sorted(
        (
            item
            for item in candidates
            if item.is_file()
            and item.name.lower().endswith(normalized_extension.lower())
        ),
        key=lambda item: str(item.relative_to(folder)).lower(),
    )
    return matches


def upload_file(
    source_file: Path,
    bucket: str,
    prefix: str,
    relative_path: Optional[Path] = None,
) -> str:
    if shutil.which("aws") is None:
        raise FileNotFoundError(
            "AWS CLI executable 'aws' was not found. Install and configure "
            "the AWS CLI before running this script."
        )

    normalized_prefix = prefix.strip("/")
    s3_base = f"s3://{bucket}"
    if normalized_prefix:
        s3_base = f"{s3_base}/{normalized_prefix}"

    if relative_path is None:
        s3_target = f"{s3_base}/"
    else:
        s3_key = relative_path.as_posix()
        s3_target = f"{s3_base}/{s3_key}"

    result = subprocess.run(
        ["aws", "s3", "cp", str(source_file), s3_target],
        check=True,
        capture_output=True,
        text=True,
    )

    if result.stdout.strip():
        print(result.stdout.strip())

    return s3_target


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if not args.path.is_dir():
        print(f"ERROR: Folder does not exist: {args.path}", file=sys.stderr)
        return 1

    try:
        recursive = args.copysubfolders == "Y"
        source_files = find_upload_files(args.path, args.ext, recursive)
        if not source_files:
            search_location = (
                f"{args.path} or its subfolders" if recursive else str(args.path)
            )
            raise ValueError(
                f"No {normalize_extension(args.ext)} files found in "
                f"{search_location}"
            )

        for source_file in source_files:
            if recursive:
                relative_path = source_file.relative_to(args.path)
            else:
                relative_path = Path(source_file.name)

            s3_target = upload_file(
                source_file,
                args.bucket,
                args.prefix,
                relative_path,
            )
            print(f"SUCCESS: Uploaded {relative_path} to {s3_target}")
    except (FileNotFoundError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        details = (error.stderr or error.stdout or str(error)).strip()
        print(f"ERROR: AWS upload failed: {details}", file=sys.stderr)
        return error.returncode or 1

    print(f"SUCCESS: Uploaded {len(source_files)} files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
