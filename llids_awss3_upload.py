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
        required=True,
        help="S3 target folder/prefix",
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


def find_upload_file(folder: Path, extension: str) -> Path:
    matches = find_upload_files(folder, extension, recursive=False)

    if len(matches) != 1:
        normalized_extension = normalize_extension(extension)
        raise ValueError(
            f"Expected exactly one {normalized_extension} file in {folder}, "
            f"but found {len(matches)}"
        )

    return matches[0]


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
    if relative_path is None:
        s3_target = f"s3://{bucket}/{normalized_prefix}/"
    else:
        s3_key = relative_path.as_posix()
        s3_target = f"s3://{bucket}/{normalized_prefix}/{s3_key}"

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
        if args.copysubfolders == "Y":
            source_files = find_upload_files(args.path, args.ext, recursive=True)
            if not source_files:
                raise ValueError(
                    f"No {normalize_extension(args.ext)} files found in "
                    f"{args.path} or its subfolders"
                )

            for source_file in source_files:
                relative_path = source_file.relative_to(args.path)
                s3_target = upload_file(
                    source_file,
                    args.bucket,
                    args.prefix,
                    relative_path,
                )
                print(f"SUCCESS: Uploaded {relative_path} to {s3_target}")

            print(f"SUCCESS: Uploaded {len(source_files)} files")
            return 0

        source_file = find_upload_file(args.path, args.ext)
        s3_target = upload_file(source_file, args.bucket, args.prefix)
    except (FileNotFoundError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        details = (error.stderr or error.stdout or str(error)).strip()
        print(f"ERROR: AWS upload failed: {details}", file=sys.stderr)
        return error.returncode or 1

    print(f"SUCCESS: Uploaded {source_file.name} to {s3_target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
