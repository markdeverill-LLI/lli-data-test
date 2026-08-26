#!/usr/bin/env python3
"""Capture AIS NMEA sentences from the Lloyd's List Intelligence TCP stream."""

from __future__ import annotations

import argparse
import datetime as dt
import ipaddress
import socket
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path


DEFAULT_HOST = "subscriber-v2.lloydslistintelligence.com"
DEFAULT_PORT = 32100
DEFAULT_RECORDS = 100
DEFAULT_IDLE_TIMEOUT_SECONDS = 120
DEFAULT_CONNECT_TIMEOUT_SECONDS = 30
DEFAULT_OUTPUT_DIR = "output"
DEFAULT_EXPECTED_PUBLIC_IP = "89.37.64.198"
DEFAULT_IP_CHECK_URL = "https://api.ipify.org"
DEFAULT_IP_CHECK_TIMEOUT_SECONDS = 10


@dataclass(frozen=True)
class PublicIpCheck:
    observed_ip: str
    expected_ip: str
    matches: bool


@dataclass(frozen=True)
class CaptureResult:
    path: Path
    record_count: int
    stop_reason: str
    local_address: str
    local_port: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Connect to the LLI AIS TCP stream, capture NMEA sentences, "
            "and write them to a timestamped .nmea file."
        )
    )
    parser.add_argument(
        "--host",
        default=DEFAULT_HOST,
        help=f"TCP host to connect to. Defaults to {DEFAULT_HOST}.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        help=f"TCP port to connect to. Defaults to {DEFAULT_PORT}.",
    )
    parser.add_argument(
        "--records",
        type=int,
        default=DEFAULT_RECORDS,
        help=f"Maximum NMEA records to capture. Defaults to {DEFAULT_RECORDS}.",
    )
    parser.add_argument(
        "--idle-timeout",
        type=float,
        default=DEFAULT_IDLE_TIMEOUT_SECONDS,
        help=(
            "Stop if no data is received for this many seconds. "
            f"Defaults to {DEFAULT_IDLE_TIMEOUT_SECONDS}."
        ),
    )
    parser.add_argument(
        "--connect-timeout",
        type=float,
        default=DEFAULT_CONNECT_TIMEOUT_SECONDS,
        help=(
            "Timeout in seconds for opening the TCP connection. "
            f"Defaults to {DEFAULT_CONNECT_TIMEOUT_SECONDS}."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default=DEFAULT_OUTPUT_DIR,
        help=f"Folder where the .nmea file will be written. Defaults to {DEFAULT_OUTPUT_DIR}.",
    )
    parser.add_argument(
        "--expected-public-ip",
        default=DEFAULT_EXPECTED_PUBLIC_IP,
        help=(
            "Expected public outbound IP address to verify before connecting. "
            f"Defaults to {DEFAULT_EXPECTED_PUBLIC_IP}."
        ),
    )
    parser.add_argument(
        "--ip-check-url",
        default=DEFAULT_IP_CHECK_URL,
        help=f"URL used to discover the public outbound IP. Defaults to {DEFAULT_IP_CHECK_URL}.",
    )
    parser.add_argument(
        "--ip-check-timeout",
        type=float,
        default=DEFAULT_IP_CHECK_TIMEOUT_SECONDS,
        help=(
            "Timeout in seconds for the public IP check. "
            f"Defaults to {DEFAULT_IP_CHECK_TIMEOUT_SECONDS}."
        ),
    )
    parser.add_argument(
        "--skip-public-ip-check",
        action="store_true",
        help="Skip the public outbound IP check before connecting to the AIS stream.",
    )
    parser.add_argument(
        "--allow-public-ip-mismatch",
        action="store_true",
        help="Continue connecting even if the observed public IP does not match the expected IP.",
    )
    return parser.parse_args()


def start_timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def output_path(output_dir: str) -> Path:
    folder = Path(output_dir).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    return folder / f"{start_timestamp()}.nmea"


def validate_ip_address(value: str, option_name: str) -> None:
    try:
        ipaddress.ip_address(value)
    except ValueError as exc:
        raise ValueError(f"{option_name} must be a valid IP address: {value}") from exc


def check_public_ip(args: argparse.Namespace) -> PublicIpCheck:
    if args.ip_check_timeout <= 0:
        raise ValueError("--ip-check-timeout must be greater than 0")

    validate_ip_address(args.expected_public_ip, "--expected-public-ip")

    request = urllib.request.Request(
        args.ip_check_url,
        headers={"User-Agent": "ais-nmea-tcp-capture/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=args.ip_check_timeout) as response:
            observed_ip = response.read(128).decode("ascii", errors="replace").strip()
    except urllib.error.URLError as exc:
        raise RuntimeError(f"public IP check failed using {args.ip_check_url}: {exc.reason}") from exc

    validate_ip_address(observed_ip, "--ip-check-url response")
    return PublicIpCheck(
        observed_ip=observed_ip,
        expected_ip=args.expected_public_ip,
        matches=observed_ip == args.expected_public_ip,
    )


def iter_nmea_records(sock: socket.socket) -> tuple[bytes, list[bytes]]:
    data = sock.recv(4096)
    if not data:
        return b"", []

    records = data.splitlines(keepends=True)
    if records and not records[-1].endswith((b"\n", b"\r")):
        return records[-1], records[:-1]

    return b"", records


def capture(args: argparse.Namespace) -> tuple[Path, int, str]:
    if args.records < 1:
        raise ValueError("--records must be at least 1")
    if args.idle_timeout <= 0:
        raise ValueError("--idle-timeout must be greater than 0")
    if args.connect_timeout <= 0:
        raise ValueError("--connect-timeout must be greater than 0")

    path = output_path(args.output_dir)
    record_count = 0
    stop_reason = f"captured requested {args.records} records"
    partial_record = b""
    local_address = ""
    local_port = 0

    try:
        sock = socket.create_connection(
            (args.host, args.port),
            timeout=args.connect_timeout,
        )
    except socket.timeout as exc:
        raise RuntimeError(
            f"TCP connection to {args.host}:{args.port} timed out after {args.connect_timeout:g} seconds"
        ) from exc
    except OSError as exc:
        raise RuntimeError(f"TCP connection to {args.host}:{args.port} failed: {exc}") from exc

    with sock:
        local_address, local_port = sock.getsockname()[:2]
        sock.settimeout(args.idle_timeout)
        with path.open("wb") as output:
            while record_count < args.records:
                try:
                    remainder, records = iter_nmea_records(sock)
                except socket.timeout:
                    stop_reason = f"no data received for {args.idle_timeout:g} seconds"
                    break

                if remainder == b"" and not records:
                    stop_reason = "remote stream closed"
                    break

                if partial_record and records:
                    records[0] = partial_record + records[0]
                    partial_record = b""
                elif partial_record and remainder:
                    partial_record += remainder
                    continue

                partial_record = remainder

                for record in records:
                    clean_record = record.strip()
                    if not clean_record:
                        continue
                    output.write(clean_record + b"\n")
                    record_count += 1
                    if record_count >= args.records:
                        break

    return CaptureResult(path, record_count, stop_reason, local_address, local_port)


def main() -> int:
    args = parse_args()

    if not args.skip_public_ip_check:
        public_ip_check = check_public_ip(args)
        status = "OK" if public_ip_check.matches else "MISMATCH"
        print(f"Public outbound IP: {public_ip_check.observed_ip} ({status})")
        print(f"Expected public IP: {public_ip_check.expected_ip}")
        if not public_ip_check.matches and not args.allow_public_ip_mismatch:
            print("Not connecting because the public outbound IP does not match.")
            print("Use --allow-public-ip-mismatch to connect anyway.")
            return 2

    print(f"Connecting to {args.host}:{args.port}")

    result = capture(args)

    print(f"TCP local endpoint: {result.local_address}:{result.local_port}")
    print(f"Output file: {result.path}")
    print(f"Stop reason: {result.stop_reason}")
    print(f"Records received: {result.record_count}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
