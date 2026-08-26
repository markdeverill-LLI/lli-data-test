#!/usr/bin/env python3
"""Create a minimal GIS-friendly vessel positions CSV from decoded AIS messages."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


POSITION_MESSAGE_TYPES = {"1", "2", "3", "18", "19", "27"}
STATIC_MESSAGE_TYPES = {"5", "24"}
OUTPUT_COLUMNS = [
    "mmsi",
    "timestamp",
    "latitude",
    "longitude",
    "cog",
    "sog",
    "source",
    "vessel_name",
    "callsign",
    "length",
    "vesseltype_code",
]


@dataclass
class VesselStatic:
    vessel_name: str = ""
    callsign: str = ""
    length: str = ""
    vesseltype_code: str = ""


@dataclass
class GisStats:
    input_rows: int = 0
    position_rows_written: int = 0
    static_rows_read: int = 0
    positions_with_static_context: int = 0
    positions_without_static_context: int = 0
    skipped_position_rows: int = 0
    geojson_tracks_written: int = 0
    geojson_tracks_skipped: int = 0


@dataclass
class VesselTrack:
    coordinates: list[list[float]]
    first_timestamp: str = ""
    last_timestamp: str = ""
    sources: set[str] | None = None
    vessel_name: str = ""
    callsign: str = ""
    length: str = ""
    vesseltype_code: str = ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read decoded AIS CSV data and write a minimal positions CSV for GIS plotting, "
            "enriching positions with the latest prior static/voyage data by MMSI."
        )
    )
    parser.add_argument("input", help="Decoded AIS CSV from ais_nmea_parse.py.")
    parser.add_argument(
        "-o",
        "--output",
        help="Output GIS positions CSV. Defaults to the input filename plus _gis_positions.csv.",
    )
    parser.add_argument(
        "--geojson-output",
        help="Optional GeoJSON FeatureCollection of per-MMSI LineString tracks for Kepler.gl.",
    )
    parser.add_argument(
        "--min-track-points",
        type=int,
        default=2,
        help="Minimum positions required to emit a GeoJSON LineString track. Defaults to 2.",
    )
    parser.add_argument(
        "--geojson-precision",
        type=int,
        default=6,
        help="Decimal places for GeoJSON coordinates. Defaults to 6.",
    )
    return parser.parse_args()


def default_output_path(input_path: Path) -> Path:
    return input_path.with_name(f"{input_path.stem}_gis_positions.csv")


def clean(value: Any) -> str:
    return "" if value is None else str(value).strip()


def int_text(value: str) -> str:
    value = clean(value)
    if not value:
        return ""
    try:
        return str(int(float(value)))
    except ValueError:
        return value


def combine_length(row: dict[str, str]) -> str:
    bow = clean(row.get("dimension_to_bow"))
    stern = clean(row.get("dimension_to_stern"))
    if not bow and not stern:
        return ""
    try:
        return str(int(float(bow or 0) + float(stern or 0)))
    except ValueError:
        return ""


def has_any_static_value(static: VesselStatic) -> bool:
    return any(
        [
            static.vessel_name,
            static.callsign,
            static.length,
            static.vesseltype_code,
        ]
    )


def to_float(value: str) -> float | None:
    value = clean(value)
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def update_static_cache(row: dict[str, str], cache: dict[str, VesselStatic]) -> bool:
    mmsi = clean(row.get("mmsi"))
    if not mmsi:
        return False

    message_type = clean(row.get("message_type"))
    if message_type not in STATIC_MESSAGE_TYPES:
        return False

    static = cache.setdefault(mmsi, VesselStatic())

    vessel_name = clean(row.get("vessel_name"))
    callsign = clean(row.get("callsign"))
    vesseltype_code = int_text(clean(row.get("ship_type")))
    length = combine_length(row)

    if vessel_name:
        static.vessel_name = vessel_name
    if callsign:
        static.callsign = callsign
    if vesseltype_code:
        static.vesseltype_code = vesseltype_code
    if length:
        static.length = length

    return any([vessel_name, callsign, vesseltype_code, length])


def position_output_row(row: dict[str, str], static: VesselStatic | None) -> dict[str, str] | None:
    mmsi = clean(row.get("mmsi"))
    latitude = clean(row.get("latitude"))
    longitude = clean(row.get("longitude"))
    if not mmsi or not latitude or not longitude:
        return None

    static = static or VesselStatic()
    return {
        "mmsi": mmsi,
        "timestamp": clean(row.get("tag_datetime_utc")) or clean(row.get("tag_unix_time")),
        "latitude": latitude,
        "longitude": longitude,
        "cog": clean(row.get("course_over_ground")),
        "sog": clean(row.get("speed_over_ground")),
        "source": clean(row.get("tag_station")) or "unknown",
        "vessel_name": static.vessel_name,
        "callsign": static.callsign,
        "length": static.length,
        "vesseltype_code": static.vesseltype_code,
    }


def update_track(
    tracks: dict[str, VesselTrack],
    output_row: dict[str, str],
    precision: int,
) -> None:
    longitude = to_float(output_row["longitude"])
    latitude = to_float(output_row["latitude"])
    if longitude is None or latitude is None:
        return

    mmsi = output_row["mmsi"]
    track = tracks.setdefault(
        mmsi,
        VesselTrack(coordinates=[], sources=set()),
    )
    track.coordinates.append([round(longitude, precision), round(latitude, precision)])

    timestamp = clean(output_row.get("timestamp"))
    if timestamp:
        if not track.first_timestamp:
            track.first_timestamp = timestamp
        track.last_timestamp = timestamp

    source = clean(output_row.get("source")) or "unknown"
    if track.sources is not None:
        track.sources.add(source)

    for attribute in ("vessel_name", "callsign", "length", "vesseltype_code"):
        value = clean(output_row.get(attribute))
        if value:
            setattr(track, attribute, value)


def write_geojson_tracks(
    tracks: dict[str, VesselTrack],
    output_path: Path,
    min_track_points: int,
) -> tuple[int, int]:
    features = []
    skipped = 0

    for mmsi, track in sorted(tracks.items()):
        if len(track.coordinates) < min_track_points:
            skipped += 1
            continue

        features.append(
            {
                "type": "Feature",
                "properties": {
                    "mmsi": mmsi,
                    "point_count": len(track.coordinates),
                    "first_timestamp": track.first_timestamp,
                    "last_timestamp": track.last_timestamp,
                    "sources": ",".join(sorted(track.sources or set())),
                    "vessel_name": track.vessel_name,
                    "callsign": track.callsign,
                    "length": track.length,
                    "vesseltype_code": track.vesseltype_code,
                },
                "geometry": {
                    "type": "LineString",
                    "coordinates": track.coordinates,
                },
            }
        )

    feature_collection = {
        "type": "FeatureCollection",
        "features": features,
    }
    with output_path.open("w", encoding="utf-8") as handle:
        json.dump(feature_collection, handle, separators=(",", ":"))
        handle.write("\n")

    return len(features), skipped


def write_gis_positions(
    input_path: Path,
    output_path: Path,
    geojson_output_path: Path | None,
    min_track_points: int,
    geojson_precision: int,
) -> GisStats:
    stats = GisStats()
    static_cache: dict[str, VesselStatic] = {}
    tracks: dict[str, VesselTrack] = {}

    if min_track_points < 2:
        raise ValueError("--min-track-points must be at least 2 for LineString output")
    if geojson_precision < 0:
        raise ValueError("--geojson-precision must be 0 or greater")

    with input_path.open("r", newline="", encoding="utf-8") as input_handle:
        reader = csv.DictReader(input_handle)
        with output_path.open("w", newline="", encoding="utf-8") as output_handle:
            writer = csv.DictWriter(output_handle, fieldnames=OUTPUT_COLUMNS)
            writer.writeheader()

            for row in reader:
                stats.input_rows += 1
                message_type = clean(row.get("message_type"))

                if message_type in STATIC_MESSAGE_TYPES:
                    if update_static_cache(row, static_cache):
                        stats.static_rows_read += 1
                    continue

                if message_type not in POSITION_MESSAGE_TYPES:
                    continue

                mmsi = clean(row.get("mmsi"))
                static = static_cache.get(mmsi)
                output_row = position_output_row(row, static)
                if output_row is None:
                    stats.skipped_position_rows += 1
                    source_line = clean(row.get("source_line")) or "unknown"
                    print(f"Skipping position row at decoded source line {source_line}: missing MMSI/latitude/longitude")
                    continue

                writer.writerow(output_row)
                if geojson_output_path is not None:
                    update_track(tracks, output_row, geojson_precision)
                stats.position_rows_written += 1
                if static is not None and has_any_static_value(static):
                    stats.positions_with_static_context += 1
                else:
                    stats.positions_without_static_context += 1

    if geojson_output_path is not None:
        written, skipped = write_geojson_tracks(tracks, geojson_output_path, min_track_points)
        stats.geojson_tracks_written = written
        stats.geojson_tracks_skipped = skipped

    return stats


def main() -> int:
    args = parse_args()
    input_path = Path(args.input).expanduser()
    if not input_path.exists():
        raise FileNotFoundError(f"input file not found: {input_path}")

    output_path = Path(args.output).expanduser() if args.output else default_output_path(input_path)
    geojson_output_path = Path(args.geojson_output).expanduser() if args.geojson_output else None
    stats = write_gis_positions(
        input_path,
        output_path,
        geojson_output_path,
        args.min_track_points,
        args.geojson_precision,
    )

    print(f"Input decoded rows: {stats.input_rows}")
    print(f"Static/voyage rows used: {stats.static_rows_read}")
    print(f"GIS position rows written: {stats.position_rows_written}")
    print(f"Positions with prior static context: {stats.positions_with_static_context}")
    print(f"Positions without prior static context: {stats.positions_without_static_context}")
    print(f"Skipped position rows: {stats.skipped_position_rows}")
    print(f"Output CSV: {output_path}")
    if geojson_output_path is not None:
        print(f"GeoJSON tracks written: {stats.geojson_tracks_written}")
        print(f"GeoJSON tracks skipped: {stats.geojson_tracks_skipped}")
        print(f"GeoJSON output: {geojson_output_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
