#!/usr/bin/env python3
"""List files in an Amazon S3 bucket folder."""

import argparse
import shutil
import subprocess
import sys
from typing import Optional, Sequence


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="List files in an Amazon S3 bucket folder"
    )
    parser.add_argument("--bucket", required=True, help="S3 bucket name")
    parser.add_argument(
        "--path",
        required=True,
        help="Folder path within the S3 bucket",
    )
    parser.add_argument(
        "-recursive",
        "--recursive",
        type=str.upper,
        choices=("Y", "N"),
        default="N",
        help="Include files in all subfolders (default: N)",
    )
    return parser


def build_s3_uri(bucket: str, folder_path: str) -> str:
    normalized_bucket = bucket.strip().strip("/")
    normalized_path = folder_path.strip().strip("/")

    if not normalized_bucket:
        raise ValueError("Bucket name cannot be empty")

    if normalized_path:
        return f"s3://{normalized_bucket}/{normalized_path}/"
    return f"s3://{normalized_bucket}/"


def list_s3_files(bucket: str, folder_path: str, recursive: bool) -> None:
    if shutil.which("aws") is None:
        raise FileNotFoundError(
            "AWS CLI executable 'aws' was not found. Install and configure "
            "the AWS CLI before running this script."
        )

    command = ["aws", "s3", "ls", build_s3_uri(bucket, folder_path)]
    if recursive:
        command.append("--recursive")

    subprocess.run(command, check=True)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        list_s3_files(
            bucket=args.bucket,
            folder_path=args.path,
            recursive=args.recursive == "Y",
        )
    except (FileNotFoundError, ValueError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as error:
        print(
            f"ERROR: AWS directory listing failed with exit code "
            f"{error.returncode}",
            file=sys.stderr,
        )
        return error.returncode or 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
