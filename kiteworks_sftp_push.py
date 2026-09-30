#!/usr/bin/env python3
"""Upload files to an SFTP server using command-line connection details."""

import argparse
import base64
import errno
import glob
import hashlib
import io
import posixpath
import sys
from pathlib import Path
from typing import List, Optional

import paramiko


class RemoteDirectoryMissingError(Exception):
    """Raised when the destination directory does not exist."""


class FingerprintPolicy(paramiko.MissingHostKeyPolicy):
    """Accept only a server host key matching the expected fingerprint."""

    def __init__(self, expected_fingerprint: str) -> None:
        self.expected_fingerprint = expected_fingerprint

    def missing_host_key(
        self,
        client: paramiko.SSHClient,
        hostname: str,
        key: paramiko.PKey,
    ) -> None:
        expected = self.expected_fingerprint.strip().split()[-1]
        sha256_fingerprint = "SHA256:" + base64.b64encode(
            hashlib.sha256(key.asbytes()).digest()
        ).decode("ascii").rstrip("=")
        md5_fingerprint = ":".join(
            f"{byte:02x}" for byte in key.get_fingerprint()
        )

        if expected.startswith("SHA256:"):
            matches = expected == sha256_fingerprint
        else:
            normalized_expected = expected.removeprefix("MD5:").lower()
            matches = normalized_expected == md5_fingerprint

        if not matches:
            raise paramiko.SSHException(
                f"Host key fingerprint mismatch for {hostname}: "
                f"expected {expected}, received {sha256_fingerprint}"
            )

        client.get_host_keys().add(hostname, key.get_name(), key)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Upload one or more files to a Kiteworks SFTP server."
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
        "--remote-path",
        required=True,
        help="Remote destination directory",
    )
    parser.add_argument(
        "--local-path",
        required=True,
        help="Local file path or wildcard pattern",
    )
    parser.add_argument(
        "--create-dir",
        action="store_true",
        help="Create the remote directory when it does not exist",
    )
    parser.add_argument(
        "--transfer-mode",
        choices=("BINARY", "ASCII"),
        default="BINARY",
        type=str.upper,
        help="File transfer mode (default: BINARY)",
    )
    args = parser.parse_args()
    if not args.password and not args.sshkey:
        parser.error("at least one of --password or --sshkey is required")
    return args


def normalize_remote_path(remote_path: str) -> str:
    normalized = remote_path.replace("\\", "/")
    if not normalized.startswith("/"):
        normalized = "/" + normalized
    if not normalized.endswith("/"):
        normalized += "/"
    return normalized


def clean_path_argument(path: str) -> str:
    cleaned = path.strip()
    quote_pairs = {'"': '"', "'": "'", "“": "”", "‘": "’"}
    if len(cleaned) >= 2 and quote_pairs.get(cleaned[0]) == cleaned[-1]:
        cleaned = cleaned[1:-1].strip()
    return cleaned


def validate_private_key_file(key_path: Path) -> None:
    with key_path.open(encoding="utf-8", errors="ignore") as key_file:
        first_line = key_file.readline().strip()

    public_key_types = {
        "ssh-rsa",
        "ssh-ed25519",
        "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521",
    }
    if (
        any(part in public_key_types for part in first_line.split()[:3])
        or first_line == "---- BEGIN SSH2 PUBLIC KEY ----"
    ):
        raise ValueError(
            f"{key_path} contains a public key. --sshkey requires the matching "
            "private key file."
        )


def find_local_files(local_path: str) -> List[Path]:
    matches = [Path(path) for path in glob.glob(local_path)]
    files = [path for path in matches if path.is_file()]
    if not files:
        raise FileNotFoundError(f"No local files matched: {local_path}")
    return files


def ensure_remote_directory(
    sftp: paramiko.SFTPClient,
    remote_path: str,
    create_dir: bool,
) -> None:
    if create_dir:
        current_path = ""
        for part in remote_path.strip("/").split("/"):
            if not part:
                continue
            current_path += "/" + part
            try:
                sftp.stat(current_path)
            except OSError as error:
                if error.errno != errno.ENOENT:
                    raise
                sftp.mkdir(current_path)
        return

    try:
        sftp.stat(remote_path)
    except OSError as error:
        if error.errno != errno.ENOENT:
            raise
        raise RemoteDirectoryMissingError(
            f"Directory {remote_path} does not exist"
        ) from error


def upload_file(
    sftp: paramiko.SFTPClient,
    local_file: Path,
    remote_file: str,
    transfer_mode: str,
) -> None:
    if transfer_mode == "ASCII":
        content = local_file.read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        with io.BytesIO(content) as source:
            sftp.putfo(source, remote_file, file_size=len(content), confirm=True)
    else:
        sftp.put(str(local_file), remote_file, confirm=True)


def configure_host_key_policy(
    client: paramiko.SSHClient,
    host_key_fingerprint: Optional[str],
) -> None:
    if host_key_fingerprint:
        client.set_missing_host_key_policy(FingerprintPolicy(host_key_fingerprint))
    else:
        print(
            "Warning: no --host-key-fingerprint supplied; "
            "server host-key verification is disabled.",
            file=sys.stderr,
        )
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())


def run_upload(args: argparse.Namespace) -> None:
    local_path = clean_path_argument(args.local_path)
    remote_path = normalize_remote_path(clean_path_argument(args.remote_path))
    local_files = find_local_files(local_path)
    ssh_key_path = None
    if args.sshkey:
        ssh_key = Path(clean_path_argument(args.sshkey)).expanduser()
        if not ssh_key.is_file():
            raise FileNotFoundError(f"SSH private key file not found: {ssh_key}")
        validate_private_key_file(ssh_key)
        ssh_key_path = str(ssh_key)

    print(f"localPath = {local_path}")
    print(f"remotePath = {remote_path}")
    print(f"create Directory = {args.create_dir}")
    print(f"transferMode = {args.transfer_mode}")

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
            ensure_remote_directory(sftp, remote_path, args.create_dir)
            for local_file in local_files:
                remote_file = posixpath.join(remote_path, local_file.name)
                upload_file(sftp, local_file, remote_file, args.transfer_mode)
                print(f"Upload of {local_file} succeeded")
    finally:
        client.close()


def main() -> int:
    args = parse_args()
    try:
        run_upload(args)
        return 0
    except RemoteDirectoryMissingError as error:
        print(error, file=sys.stderr)
        return 5
    except Exception as error:
        print(error, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
