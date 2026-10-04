#!/usr/bin/env python3
"""Download files from an SFTP server."""

import argparse
import fnmatch
import stat
import sys
from contextlib import nullcontext
from pathlib import Path, PurePosixPath
from typing import TextIO

import paramiko

from llids_sftp_list import iter_directory, resolve_private_key
from llids_sftp_push import clean_path_argument, configure_host_key_policy


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download files from an LLIDS SFTP server."
    )
    parser.add_argument("--server", required=True, help="SFTP server hostname")
    parser.add_argument("--port", type=int, default=22, help="SFTP port (default: 22)")
    parser.add_argument("--user", required=True, help="SFTP username")
    parser.add_argument("--password", help="SFTP account password")
    parser.add_argument(
        "--sshkey",
        "--ssh-key",
        dest="sshkey",
        help="Path to the client SSH private-key file",
    )
    parser.add_argument(
        "--key-passphrase",
        help="Passphrase used to decrypt the client SSH private key",
    )
    parser.add_argument(
        "--host-key-fingerprint",
        help="Expected server SSH host-key fingerprint",
    )
    parser.add_argument(
        "--directoryroot",
        help="Remote directory to download from; defaults to the login directory",
    )
    parser.add_argument(
        "--subfolders",
        choices=("Y", "N"),
        default="N",
        type=str.upper,
        help="Recursively download from subfolders: Y or N (default: N)",
    )
    parser.add_argument(
        "--outputfile",
        help="Write the download results to this local file",
    )
    parser.add_argument(
        "--file-pattern",
        default="*",
        help="Shell-style file pattern to download (default: *)",
    )
    parser.add_argument(
        "--destination-folder",
        required=True,
        help="Local folder where downloaded files will be written",
    )
    args = parser.parse_args()
    if not args.password and not args.sshkey:
        parser.error("at least one of --password or --sshkey is required")
    return args


def matches_file_pattern(remote_path: str, filename: str, pattern: str) -> bool:
    return fnmatch.fnmatch(filename, pattern) or fnmatch.fnmatch(remote_path, pattern)


def local_download_path(destination: Path, remote_path: str) -> Path:
    remote_parts = PurePosixPath(remote_path).parts
    if not remote_parts or any(part in ("", ".", "..", "/") for part in remote_parts):
        raise ValueError(f"Unsafe remote path returned by server: {remote_path}")
    return destination.joinpath(*remote_parts)


def download_files(
    sftp: paramiko.SFTPClient,
    destination: Path,
    recursive: bool,
    file_pattern: str,
    output: TextIO,
) -> int:
    downloaded = 0
    print("SIZE\tREMOTE_PATH\tLOCAL_PATH", file=output)

    for remote_path, entry in iter_directory(sftp, recursive=recursive):
        if stat.S_ISDIR(entry.st_mode):
            continue
        if not matches_file_pattern(remote_path, entry.filename, file_pattern):
            continue

        local_path = local_download_path(destination, remote_path)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {remote_path} to {local_path}...", file=sys.stderr)
        sftp.get(remote_path, str(local_path))

        local_size = local_path.stat().st_size
        if entry.st_size is not None and local_size != entry.st_size:
            raise OSError(
                f"Downloaded size mismatch for {remote_path}: "
                f"expected {entry.st_size}, received {local_size}"
            )

        print(f"{local_size}\t{remote_path}\t{local_path}", file=output)
        downloaded += 1

    return downloaded


def run_pull(args: argparse.Namespace) -> None:
    ssh_key_path = resolve_private_key(args.sshkey) if args.sshkey else None
    destination = Path(
        clean_path_argument(args.destination_folder)
    ).expanduser()
    destination.mkdir(parents=True, exist_ok=True)

    client = paramiko.SSHClient()
    configure_host_key_policy(client, args.host_key_fingerprint)

    try:
        client.connect(
            hostname=args.server,
            port=args.port,
            key_filename=ssh_key_path,
            passphrase=args.key_passphrase,
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
            with output_context as output:
                downloaded = download_files(
                    sftp=sftp,
                    destination=destination,
                    recursive=args.subfolders == "Y",
                    file_pattern=args.file_pattern,
                    output=output,
                )
                print(f"Downloaded {downloaded} file(s).", file=output)
    finally:
        client.close()


def main() -> int:
    args = parse_args()
    try:
        run_pull(args)
        return 0
    except paramiko.PasswordRequiredException:
        print(
            "Download failed: private key file is encrypted. Supply its "
            "passphrase with --key-passphrase.",
            file=sys.stderr,
        )
        return 1
    except Exception as error:
        print(f"Download failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
