#!/usr/bin/env python3
"""List files and directories on an SFTP server."""

import argparse
import posixpath
import stat
import sys
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, TextIO

import paramiko

from kiteworks_sftp_push import (
    clean_path_argument,
    configure_host_key_policy,
    validate_private_key_file,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="List files and directories on a Kiteworks SFTP server."
    )
    parser.add_argument("--server", required=True, help="SFTP server hostname")
    parser.add_argument("--port", type=int, default=22, help="SFTP port (default: 22)")
    parser.add_argument("--user", required=True, help="SFTP username")
    parser.add_argument("--password", help="SFTP password or private-key passphrase")
    parser.add_argument(
        "--sshkey",
        "--ssh-key",
        dest="sshkey",
        help="Path to the client SSH private-key file",
    )
    parser.add_argument(
        "--host-key-fingerprint",
        help="Expected server SSH host-key fingerprint",
    )
    parser.add_argument(
        "--directoryroot",
        help="Remote directory to list; defaults to the login directory",
    )
    parser.add_argument(
        "--subfolders",
        choices=("Y", "N"),
        default="N",
        type=str.upper,
        help="Recursively list subfolders: Y or N (default: N)",
    )
    parser.add_argument(
        "--outputfile",
        help="Write the directory listing to this local file",
    )
    args = parser.parse_args()
    if not args.password and not args.sshkey:
        parser.error("at least one of --password or --sshkey is required")
    return args


def resolve_private_key(sshkey: str) -> str:
    key_path = Path(clean_path_argument(sshkey)).expanduser()
    if not key_path.is_file():
        raise FileNotFoundError(f"SSH private key file not found: {key_path}")
    validate_private_key_file(key_path)
    return str(key_path)


def iter_directory(
    sftp: paramiko.SFTPClient,
    directory: str = ".",
    recursive: bool = False,
) -> Iterable[tuple[str, paramiko.SFTPAttributes]]:
    entries = sorted(sftp.listdir_attr(directory), key=lambda entry: entry.filename)
    for entry in entries:
        entry_path = (
            entry.filename
            if directory == "."
            else posixpath.join(directory, entry.filename)
        )
        yield entry_path, entry
        if recursive and stat.S_ISDIR(entry.st_mode):
            yield from iter_directory(sftp, entry_path, recursive=True)


def format_entry(path: str, entry: paramiko.SFTPAttributes) -> str:
    is_directory = stat.S_ISDIR(entry.st_mode)
    entry_type = "DIR" if is_directory else "FILE"
    size = "" if is_directory or entry.st_size is None else str(entry.st_size)
    modified = (
        ""
        if entry.st_mtime is None
        else datetime.fromtimestamp(
            entry.st_mtime,
            tz=timezone.utc,
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    display_path = path + "/" if is_directory else path
    return f"{entry_type}\t{size}\t{modified}\t{display_path}"


def write_listing(
    sftp: paramiko.SFTPClient,
    recursive: bool,
    destination: TextIO,
) -> None:
    print("TYPE\tSIZE\tMODIFIED_UTC\tPATH", file=destination)
    for path, entry in iter_directory(sftp, recursive=recursive):
        print(format_entry(path, entry), file=destination)


def run_listing(args: argparse.Namespace) -> None:
    ssh_key_path = resolve_private_key(args.sshkey) if args.sshkey else None
    client = paramiko.SSHClient()
    configure_host_key_policy(client, args.host_key_fingerprint)

    try:
        client.connect(
            hostname=args.server,
            port=args.port,
            key_filename=ssh_key_path,
            username=args.user,
            password=args.password,
            allow_agent=False,
            look_for_keys=False,
        )
        with client.open_sftp() as sftp:
            if args.directoryroot:
                sftp.chdir(clean_path_argument(args.directoryroot))

            output_context = (
                Path(clean_path_argument(args.outputfile))
                .expanduser()
                .open("w", encoding="utf-8", newline="\n")
                if args.outputfile
                else nullcontext(sys.stdout)
            )
            with output_context as destination:
                write_listing(
                    sftp,
                    recursive=args.subfolders == "Y",
                    destination=destination,
                )
    finally:
        client.close()


def main() -> int:
    args = parse_args()
    try:
        run_listing(args)
        return 0
    except Exception as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
