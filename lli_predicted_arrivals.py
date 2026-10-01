#!/usr/bin/env python3
"""Download all pages of LLI predicted arrivals and write a flattened CSV."""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


API_URL = "https://api.lloydslistintelligence.com/v1/predictedarrivals_v3"
COUNTRY_RE = re.compile(r"^[A-Z]{3}$")


class PredictedArrivalsError(RuntimeError):
    """A clear, user-facing failure."""


def read_token(path: Path) -> str:
    try:
        with path.open(encoding="utf-8") as handle:
            token_data = json.load(handle)
    except OSError as exc:
        raise PredictedArrivalsError(f"Could not read token file {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise PredictedArrivalsError(f"Token file is not valid JSON: {path}") from exc
    if not isinstance(token_data, dict):
        raise PredictedArrivalsError(f"Token file must contain a JSON object: {path}")
    token = token_data.get("token")
    if not isinstance(token, str):
        raise PredictedArrivalsError(
            f"Token file does not contain a string 'token' value: {path}"
        )
    token = token.strip()
    if not token:
        raise PredictedArrivalsError(f"Token value is empty in: {path}")
    return token


def default_date_range(today: date | None = None) -> tuple[date, date]:
    """Return tomorrow through four days after tomorrow (five dates total)."""
    first = (today or date.today()) + timedelta(days=1)
    return first, first + timedelta(days=4)


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Invalid date {value!r}; expected YYYY-MM-DD"
        ) from exc


def page_details(payload: dict[str, Any]) -> tuple[int, int, list[dict[str, Any]]]:
    if not payload.get("IsSuccess", False):
        raise PredictedArrivalsError(
            f"API reported failure: {json.dumps(payload.get('Errors', []), ensure_ascii=False)}"
        )
    data = payload.get("Data")
    if not isinstance(data, dict):
        raise PredictedArrivalsError("API response does not contain a Data object")
    try:
        current_page = int(data["CurrentPage"])
        total_pages = int(data["TotalPages"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PredictedArrivalsError(
            "API response has invalid Data.CurrentPage or Data.TotalPages"
        ) from exc
    items = data.get("Items")
    if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
        raise PredictedArrivalsError("API response Data.Items is not a list of objects")
    if current_page < 1 or total_pages < 1 or current_page > total_pages:
        raise PredictedArrivalsError(
            f"Invalid pagination values: CurrentPage={current_page}, TotalPages={total_pages}"
        )
    return current_page, total_pages, items


def fetch_page(
    *,
    country: str,
    start_date: date,
    end_date: date,
    page_number: int,
    token: str,
    authorization_prefix: str,
    timeout: float,
    retries: int,
) -> dict[str, Any]:
    query = urlencode(
        {
            "country": country,
            "dateRange": f"{start_date.isoformat()} - {end_date.isoformat()}",
            "output": "json",
            "congestion": "true",
            "pageNumber": page_number,
        }
    )
    request = Request(
        f"{API_URL}?{query}",
        headers={
            "Authorization": f"{authorization_prefix}{token}",
            "Accept": "application/json",
            "User-Agent": "lli-predicted-arrivals-downloader/1.0",
        },
    )

    for attempt in range(retries + 1):
        try:
            with urlopen(request, timeout=timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                body = response.read().decode(charset)
            payload = json.loads(body)
            if not isinstance(payload, dict):
                raise PredictedArrivalsError("API returned JSON that is not an object")
            return payload
        except HTTPError as exc:
            detail = exc.read(1000).decode("utf-8", errors="replace")
            retryable = exc.code == 429 or 500 <= exc.code < 600
            if attempt < retries and retryable:
                time.sleep(2**attempt)
                continue
            raise PredictedArrivalsError(
                f"API request failed with HTTP {exc.code}: {detail}"
            ) from exc
        except (URLError, TimeoutError) as exc:
            if attempt < retries:
                time.sleep(2**attempt)
                continue
            raise PredictedArrivalsError(f"API request failed: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise PredictedArrivalsError("API returned invalid JSON") from exc
    raise AssertionError("unreachable")


def write_source_json(path: Path, payload: dict[str, Any]) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


def flatten_object(
    value: dict[str, Any], prefix: str = "", output: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Flatten nested objects; preserve arrays as compact JSON values."""
    result = output if output is not None else {}
    for key, child in value.items():
        column = f"{prefix}.{key}" if prefix else key
        if isinstance(child, dict):
            flatten_object(child, column, result)
        elif isinstance(child, list):
            result[column] = json.dumps(
                child, ensure_ascii=False, separators=(",", ":")
            )
        elif child is None:
            result[column] = ""
        else:
            result[column] = child
    return result


def translated_csv_path(path: str) -> str:
    """Translate API object names to the requested abbreviated CSV names."""
    parts = path.split(".")
    translated: list[str] = []
    for index, part in enumerate(parts):
        if index == 0 and part == "place":
            translated.append("plc")
        elif index == 0 and part == "predictedArrival":
            translated.append("PrdArr")
        elif index == 1 and parts[0] == "predictedArrival":
            if part == "vessel":
                translated.append("Vsl")
            elif part == "previousCalling":
                translated.append("prevCall")
            elif part == "latestPosition":
                translated.append("LastPos")
            elif part.startswith("predictedDestination"):
                suffix = part.removeprefix("predictedDestination")
                translated.append(f"prdDest{suffix}")
            else:
                translated.append(part)
        else:
            translated.append(part)
    return ".".join(translated)


def translate_row_columns(row: dict[str, Any]) -> dict[str, Any]:
    return {translated_csv_path(key): value for key, value in row.items()}


def arrival_rows(
    pages: Iterable[tuple[int, list[dict[str, Any]]]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for page_number, items in pages:
        for item in items:
            arrivals = item.get("predictedArrivals")
            port_data = {key: value for key, value in item.items() if key != "predictedArrivals"}
            base = flatten_object(port_data)
            base["_source_page"] = page_number
            if isinstance(arrivals, list) and arrivals:
                for arrival in arrivals:
                    row = dict(base)
                    if isinstance(arrival, dict):
                        flatten_object(arrival, "predictedArrival", row)
                    else:
                        row["predictedArrival"] = json.dumps(
                            arrival, ensure_ascii=False, separators=(",", ":")
                        )
                    rows.append(translate_row_columns(row))
            else:
                rows.append(translate_row_columns(base))
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)
    if not fieldnames:
        fieldnames = ["_source_page"]

    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Download every page of LLI predicted arrivals and create one flattened CSV."
        )
    )
    parser.add_argument("country", help="three-letter country code, e.g. USA, MEX, CAN")
    parser.add_argument(
        "--token-file",
        "--token_file",
        type=Path,
        default=Path("LLI_API_TOKEN.json"),
        help=(
            "JSON file containing the LLI authorisation token under the 'token' key "
            "(default: ./LLI_API_TOKEN.json)"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("."),
        help="directory for page JSON files and the CSV (default: current directory)",
    )
    parser.add_argument(
        "--start-date",
        type=parse_date,
        help="first arrival date, YYYY-MM-DD (default: tomorrow)",
    )
    parser.add_argument(
        "--end-date",
        type=parse_date,
        help="last arrival date, YYYY-MM-DD (default: four days after start date)",
    )
    parser.add_argument(
        "--authorization-prefix",
        default="",
        help="optional text before the token in the Authorization header (default: none)",
    )
    parser.add_argument("--timeout", type=float, default=60.0, help="request timeout seconds")
    parser.add_argument(
        "--retries",
        type=int,
        default=3,
        help="retries for rate limits, server errors, and network failures",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    country = args.country.upper()
    if not COUNTRY_RE.fullmatch(country):
        raise PredictedArrivalsError(
            f"Country must be a three-letter code such as USA, MEX, or CAN: {args.country!r}"
        )
    if args.timeout <= 0 or args.retries < 0:
        raise PredictedArrivalsError("--timeout must be positive and --retries non-negative")

    default_start, _ = default_date_range()
    start_date = args.start_date or default_start
    end_date = args.end_date or (start_date + timedelta(days=4))
    if end_date < start_date:
        raise PredictedArrivalsError("--end-date must not be earlier than --start-date")

    token = read_token(args.token_file.expanduser())
    output_dir = args.output_dir.expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_timestamp = datetime.now().astimezone().strftime("%Y%m%d%H%M")
    base_name = f"predictedarrivals_{country}_{run_timestamp}"

    pages: list[tuple[int, list[dict[str, Any]]]] = []
    requested_page = 1
    expected_total: int | None = None
    while True:
        print(f"Requesting page {requested_page}...", file=sys.stderr)
        payload = fetch_page(
            country=country,
            start_date=start_date,
            end_date=end_date,
            page_number=requested_page,
            token=token,
            authorization_prefix=args.authorization_prefix,
            timeout=args.timeout,
            retries=args.retries,
        )
        current_page, total_pages, items = page_details(payload)
        if current_page != requested_page:
            raise PredictedArrivalsError(
                f"Requested page {requested_page}, but API returned CurrentPage={current_page}"
            )
        if expected_total is not None and total_pages != expected_total:
            raise PredictedArrivalsError(
                f"TotalPages changed during download: {expected_total} to {total_pages}"
            )
        expected_total = total_pages

        json_path = output_dir / f"{base_name}_page{current_page}.json"
        write_source_json(json_path, payload)
        pages.append((current_page, items))
        print(
            f"Saved page {current_page}/{total_pages} ({len(items)} port records): {json_path}",
            file=sys.stderr,
        )
        if current_page == total_pages:
            break
        requested_page = current_page + 1

    rows = arrival_rows(pages)
    csv_path = output_dir / f"{base_name}.csv"
    write_csv(csv_path, rows)
    print(f"Saved {len(rows)} flattened arrival rows: {csv_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PredictedArrivalsError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
