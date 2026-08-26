#!/usr/bin/env python3
"""Decode captured AIS NMEA data to CSV."""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SUPPORTED_MESSAGE_TYPES = {1, 2, 3, 5, 18, 19, 24, 27}
CSV_COLUMNS = [
    "source_line",
    "sentence_type",
    "tag_station",
    "tag_unix_time",
    "tag_datetime_utc",
    "tag_group",
    "ais_channel",
    "message_type",
    "repeat_indicator",
    "mmsi",
    "part_number",
    "navigation_status",
    "navigation_status_text",
    "rate_of_turn",
    "rate_of_turn_degrees_per_minute",
    "speed_over_ground",
    "position_accuracy",
    "longitude",
    "latitude",
    "course_over_ground",
    "true_heading",
    "timestamp",
    "maneuver_indicator",
    "raim",
    "radio_status",
    "ais_version",
    "imo_number",
    "callsign",
    "vessel_name",
    "ship_type",
    "ship_type_text",
    "dimension_to_bow",
    "dimension_to_stern",
    "dimension_to_port",
    "dimension_to_starboard",
    "epfd_type",
    "epfd_type_text",
    "eta_month",
    "eta_day",
    "eta_hour",
    "eta_minute",
    "draught",
    "destination",
    "dte",
    "reserved",
    "reserved_2",
    "spare",
    "cs_unit",
    "display_flag",
    "dsc_flag",
    "band_flag",
    "message_22_flag",
    "assigned_mode_flag",
    "gnss_position_status",
    "vendor_id",
    "vendor_manufacturer_id",
    "unit_model_code",
    "serial_number",
    "mothership_mmsi",
    "nmea_valid_checksum_count",
    "nmea_invalid_checksum_count",
    "payload",
    "fill_bits",
]

STATS_COLUMNS = [
    "message_type",
    "ais_source",
    "message_count",
    "unique_mmsi_count",
    "min_tag_unix_time",
    "max_tag_unix_time",
    "min_tag_datetime_utc",
    "max_tag_datetime_utc",
    "valid_checksum_count",
    "invalid_checksum_count",
]

NAVIGATION_STATUS = {
    0: "under way using engine",
    1: "at anchor",
    2: "not under command",
    3: "restricted manoeuverability",
    4: "constrained by her draught",
    5: "moored",
    6: "aground",
    7: "engaged in fishing",
    8: "under way sailing",
    9: "reserved for high speed craft",
    10: "reserved for wing in ground",
    11: "reserved",
    12: "reserved",
    13: "reserved",
    14: "AIS-SART active",
    15: "not defined",
}

SHIP_TYPES = {
    0: "not available",
    20: "wing in ground",
    30: "fishing",
    31: "towing",
    32: "towing large",
    33: "dredging or underwater ops",
    34: "diving ops",
    35: "military ops",
    36: "sailing",
    37: "pleasure craft",
    40: "high speed craft",
    50: "pilot vessel",
    51: "search and rescue vessel",
    52: "tug",
    53: "port tender",
    54: "anti-pollution equipment",
    55: "law enforcement",
    58: "medical transport",
    59: "noncombatant ship",
    60: "passenger",
    70: "cargo",
    80: "tanker",
    90: "other type",
}

EPFD_TYPES = {
    0: "undefined",
    1: "GPS",
    2: "GLONASS",
    3: "combined GPS/GLONASS",
    4: "Loran-C",
    5: "Chayka",
    6: "integrated navigation system",
    7: "surveyed",
    8: "Galileo",
    15: "internal GNSS",
}


@dataclass(frozen=True)
class NmeaSentence:
    line_number: int
    raw_line: str
    sentence_type: str
    total_fragments: int
    fragment_number: int
    sequence_id: str
    channel: str
    payload: str
    fill_bits: int
    tag_values: dict[str, str]
    valid_checksum_count: int
    invalid_checksum_count: int


@dataclass(frozen=True)
class ParseStats:
    input_lines: int = 0
    nmea_sentences: int = 0
    assembled_messages: int = 0
    decoded_messages: int = 0
    skipped_message_types: int = 0
    decode_errors: int = 0
    invalid_lines: int = 0
    incomplete_multipart: int = 0


@dataclass
class MessageTypeSourceStats:
    message_type: int
    ais_source: str
    message_count: int = 0
    mmsis: set[str] | None = None
    min_tag_unix_time: int | None = None
    max_tag_unix_time: int | None = None
    valid_checksum_count: int = 0
    invalid_checksum_count: int = 0


@dataclass(frozen=True)
class DecodeError:
    source_line: int
    message_type: int | None
    mmsi: int | None
    error: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Decode AIS NMEA capture files to structured CSV rows."
    )
    parser.add_argument("input", help="Input .nmea file captured from the AIS TCP stream.")
    parser.add_argument(
        "-o",
        "--output",
        help="Output CSV path. Defaults to the input filename with a .csv suffix.",
    )
    parser.add_argument(
        "--stats-output",
        help="Statistics CSV path. Defaults to the decoded output filename plus _stats.csv.",
    )
    parser.add_argument(
        "--include-unsupported",
        action="store_true",
        help="Emit minimal CSV rows for AIS message types outside the requested decoder set.",
    )
    parser.add_argument(
        "--strict-checksum",
        action="store_true",
        help="Reject NMEA sentences whose checksum does not validate.",
    )
    return parser.parse_args()


def sixbit_value(char: str) -> int:
    value = ord(char) - 48
    if value > 40:
        value -= 8
    if value < 0 or value > 63:
        raise ValueError(f"invalid AIS payload character: {char!r}")
    return value


def payload_to_bits(payload: str, fill_bits: int) -> str:
    bits = "".join(f"{sixbit_value(char):06b}" for char in payload)
    if fill_bits:
        bits = bits[:-fill_bits]
    return bits


def uint(bits: str, start: int, length: int) -> int:
    segment = bits[start : start + length]
    if len(segment) < length:
        raise ValueError(f"message ended before bit {start + length}")
    return int(segment, 2)


def sint(bits: str, start: int, length: int) -> int:
    value = uint(bits, start, length)
    sign_bit = 1 << (length - 1)
    return value - (1 << length) if value & sign_bit else value


def ais_text(bits: str, start: int, length: int) -> str:
    chars = []
    for index in range(start, start + length, 6):
        value = uint(bits, index, 6)
        chars.append(chr(value + 64) if value < 32 else chr(value))
    return "".join(chars).replace("@", " ").strip()


def safe_lookup(mapping: dict[int, str], value: int) -> str:
    if value in mapping:
        return mapping[value]
    if 40 <= value <= 49:
        return "high speed craft"
    if 60 <= value <= 69:
        return "passenger"
    if 70 <= value <= 79:
        return "cargo"
    if 80 <= value <= 89:
        return "tanker"
    return ""


def decimal_longitude(raw_value: int, scale: int = 600000) -> float | None:
    if raw_value in (0x6791AC0, 181 * scale):
        return None
    return raw_value / scale


def decimal_latitude(raw_value: int, scale: int = 600000) -> float | None:
    if raw_value in (0x3412140, 91 * scale):
        return None
    return raw_value / scale


def sog(raw_value: int) -> float | None:
    return None if raw_value == 1023 else raw_value / 10


def cog(raw_value: int) -> float | None:
    return None if raw_value == 3600 else raw_value / 10


def true_heading(raw_value: int) -> int | None:
    return None if raw_value == 511 else raw_value


def rate_of_turn(raw_value: int) -> float | None:
    if raw_value == -128:
        return None
    if raw_value in (-127, 127):
        return None
    return (raw_value / 4.733) ** 2 * (-1 if raw_value < 0 else 1)


def eta_value(value: int, unavailable: int) -> int | None:
    return None if value == unavailable else value


def draught(raw_value: int) -> float | None:
    return None if raw_value == 0 else raw_value / 10


def nmea_checksum_ok(sentence: str) -> bool:
    if not sentence.startswith(("!", "$")) or "*" not in sentence:
        return False
    body, expected = sentence[1:].split("*", 1)
    checksum = 0
    for char in body:
        checksum ^= ord(char)
    try:
        return checksum == int(expected[:2], 16)
    except ValueError:
        return False


def split_tag_block(line: str) -> tuple[dict[str, str], str]:
    tag_values: dict[str, str] = {}
    if not line.startswith("\\"):
        return tag_values, line

    end = line.find("\\", 1)
    if end == -1:
        return tag_values, line

    tag_body = line[1:end]
    if "*" in tag_body:
        tag_body = tag_body.split("*", 1)[0]

    for item in tag_body.split(","):
        if ":" in item:
            key, value = item.split(":", 1)
            tag_values[key] = value

    return tag_values, line[end + 1 :]


def parse_nmea_line(line: str, line_number: int, strict_checksum: bool) -> NmeaSentence | None:
    tag_values, sentence = split_tag_block(line.strip())
    start = sentence.find("!")
    if start == -1:
        start = sentence.find("$")
    if start == -1:
        return None
    sentence = sentence[start:]

    checksum_valid = nmea_checksum_ok(sentence)
    if strict_checksum and not checksum_valid:
        raise ValueError(f"line {line_number}: invalid NMEA checksum")

    body = sentence[1:].split("*", 1)[0]
    fields = body.split(",")
    if len(fields) < 7 or fields[0] not in {"AIVDM", "AIVDO"}:
        return None

    return NmeaSentence(
        line_number=line_number,
        raw_line=line.rstrip("\n"),
        sentence_type=fields[0],
        total_fragments=int(fields[1]),
        fragment_number=int(fields[2]),
        sequence_id=fields[3],
        channel=fields[4],
        payload=fields[5],
        fill_bits=int(fields[6] or 0),
        tag_values=tag_values,
        valid_checksum_count=1 if checksum_valid else 0,
        invalid_checksum_count=0 if checksum_valid else 1,
    )


def tag_datetime_utc(tag_values: dict[str, str]) -> str:
    unix_time = tag_values.get("c")
    if not unix_time:
        return ""
    try:
        timestamp = int(unix_time)
    except ValueError:
        return ""
    return dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).isoformat()


def base_row(sentence: NmeaSentence, message_type: int, bits: str) -> dict[str, Any]:
    row = {column: "" for column in CSV_COLUMNS}
    row.update(
        {
            "source_line": sentence.line_number,
            "sentence_type": sentence.sentence_type,
            "tag_station": sentence.tag_values.get("s", ""),
            "tag_unix_time": sentence.tag_values.get("c", ""),
            "tag_datetime_utc": tag_datetime_utc(sentence.tag_values),
            "tag_group": sentence.tag_values.get("g", ""),
            "ais_channel": sentence.channel,
            "message_type": message_type,
            "repeat_indicator": uint(bits, 6, 2),
            "mmsi": uint(bits, 8, 30),
            "nmea_valid_checksum_count": sentence.valid_checksum_count,
            "nmea_invalid_checksum_count": sentence.invalid_checksum_count,
            "payload": sentence.payload,
            "fill_bits": sentence.fill_bits,
        }
    )
    return row


def add_position_common(row: dict[str, Any], bits: str) -> None:
    nav_status = uint(bits, 38, 4)
    rot_raw = sint(bits, 42, 8)
    row.update(
        {
            "navigation_status": nav_status,
            "navigation_status_text": NAVIGATION_STATUS.get(nav_status, ""),
            "rate_of_turn": rot_raw,
            "rate_of_turn_degrees_per_minute": rate_of_turn(rot_raw),
            "speed_over_ground": sog(uint(bits, 50, 10)),
            "position_accuracy": uint(bits, 60, 1),
            "longitude": decimal_longitude(sint(bits, 61, 28)),
            "latitude": decimal_latitude(sint(bits, 89, 27)),
            "course_over_ground": cog(uint(bits, 116, 12)),
            "true_heading": true_heading(uint(bits, 128, 9)),
            "timestamp": uint(bits, 137, 6),
            "maneuver_indicator": uint(bits, 143, 2),
            "spare": uint(bits, 145, 3),
            "raim": uint(bits, 148, 1),
            "radio_status": uint(bits, 149, 19),
        }
    )


def decode_type_1_2_3(sentence: NmeaSentence, bits: str, message_type: int) -> dict[str, Any]:
    row = base_row(sentence, message_type, bits)
    add_position_common(row, bits)
    return row


def decode_type_5(sentence: NmeaSentence, bits: str, message_type: int) -> dict[str, Any]:
    row = base_row(sentence, message_type, bits)
    ship_type = uint(bits, 232, 8)
    epfd_type = uint(bits, 270, 4)
    row.update(
        {
            "ais_version": uint(bits, 38, 2),
            "imo_number": uint(bits, 40, 30),
            "callsign": ais_text(bits, 70, 42),
            "vessel_name": ais_text(bits, 112, 120),
            "ship_type": ship_type,
            "ship_type_text": safe_lookup(SHIP_TYPES, ship_type),
            "dimension_to_bow": uint(bits, 240, 9),
            "dimension_to_stern": uint(bits, 249, 9),
            "dimension_to_port": uint(bits, 258, 6),
            "dimension_to_starboard": uint(bits, 264, 6),
            "epfd_type": epfd_type,
            "epfd_type_text": safe_lookup(EPFD_TYPES, epfd_type),
            "eta_month": eta_value(uint(bits, 274, 4), 0),
            "eta_day": eta_value(uint(bits, 278, 5), 0),
            "eta_hour": eta_value(uint(bits, 283, 5), 24),
            "eta_minute": eta_value(uint(bits, 288, 6), 60),
            "draught": draught(uint(bits, 294, 8)),
            "destination": ais_text(bits, 302, 120),
            "dte": uint(bits, 422, 1),
            "spare": uint(bits, 423, 1),
        }
    )
    return row


def decode_type_18(sentence: NmeaSentence, bits: str, message_type: int) -> dict[str, Any]:
    row = base_row(sentence, message_type, bits)
    row.update(
        {
            "reserved": uint(bits, 38, 8),
            "speed_over_ground": sog(uint(bits, 46, 10)),
            "position_accuracy": uint(bits, 56, 1),
            "longitude": decimal_longitude(sint(bits, 57, 28)),
            "latitude": decimal_latitude(sint(bits, 85, 27)),
            "course_over_ground": cog(uint(bits, 112, 12)),
            "true_heading": true_heading(uint(bits, 124, 9)),
            "timestamp": uint(bits, 133, 6),
            "reserved_2": uint(bits, 139, 2),
            "cs_unit": uint(bits, 141, 1),
            "display_flag": uint(bits, 142, 1),
            "dsc_flag": uint(bits, 143, 1),
            "band_flag": uint(bits, 144, 1),
            "message_22_flag": uint(bits, 145, 1),
            "assigned_mode_flag": uint(bits, 146, 1),
            "raim": uint(bits, 147, 1),
            "radio_status": uint(bits, 148, 20),
        }
    )
    return row


def decode_type_19(sentence: NmeaSentence, bits: str, message_type: int) -> dict[str, Any]:
    row = base_row(sentence, message_type, bits)
    ship_type = uint(bits, 263, 8)
    epfd_type = uint(bits, 301, 4)
    row.update(
        {
            "reserved": uint(bits, 38, 8),
            "speed_over_ground": sog(uint(bits, 46, 10)),
            "position_accuracy": uint(bits, 56, 1),
            "longitude": decimal_longitude(sint(bits, 57, 28)),
            "latitude": decimal_latitude(sint(bits, 85, 27)),
            "course_over_ground": cog(uint(bits, 112, 12)),
            "true_heading": true_heading(uint(bits, 124, 9)),
            "timestamp": uint(bits, 133, 6),
            "reserved_2": uint(bits, 139, 4),
            "vessel_name": ais_text(bits, 143, 120),
            "ship_type": ship_type,
            "ship_type_text": safe_lookup(SHIP_TYPES, ship_type),
            "dimension_to_bow": uint(bits, 271, 9),
            "dimension_to_stern": uint(bits, 280, 9),
            "dimension_to_port": uint(bits, 289, 6),
            "dimension_to_starboard": uint(bits, 295, 6),
            "epfd_type": epfd_type,
            "epfd_type_text": safe_lookup(EPFD_TYPES, epfd_type),
            "raim": uint(bits, 305, 1),
            "dte": uint(bits, 306, 1),
            "assigned_mode_flag": uint(bits, 307, 1),
            "spare": uint(bits, 308, 4),
        }
    )
    return row


def decode_type_24(sentence: NmeaSentence, bits: str, message_type: int) -> dict[str, Any]:
    row = base_row(sentence, message_type, bits)
    part_number = uint(bits, 38, 2)
    row["part_number"] = part_number
    if part_number == 0:
        row["vessel_name"] = ais_text(bits, 40, 120)
    elif part_number == 1:
        ship_type = uint(bits, 40, 8)
        row.update(
            {
                "ship_type": ship_type,
                "ship_type_text": safe_lookup(SHIP_TYPES, ship_type),
                "vendor_id": ais_text(bits, 48, 42),
                "vendor_manufacturer_id": ais_text(bits, 48, 18),
                "unit_model_code": uint(bits, 66, 4),
                "serial_number": uint(bits, 70, 20),
                "callsign": ais_text(bits, 90, 42),
            }
        )
        if str(row["mmsi"]).startswith("98"):
            row["mothership_mmsi"] = uint(bits, 132, 30)
        else:
            row.update(
                {
                    "dimension_to_bow": uint(bits, 132, 9),
                    "dimension_to_stern": uint(bits, 141, 9),
                    "dimension_to_port": uint(bits, 150, 6),
                    "dimension_to_starboard": uint(bits, 156, 6),
                    "spare": uint(bits, 162, 6),
                }
            )
    return row


def decode_type_27(sentence: NmeaSentence, bits: str, message_type: int) -> dict[str, Any]:
    row = base_row(sentence, message_type, bits)
    nav_status = uint(bits, 40, 4)
    sog_raw = uint(bits, 79, 6)
    cog_raw = uint(bits, 85, 9)
    row.update(
        {
            "position_accuracy": uint(bits, 38, 1),
            "raim": uint(bits, 39, 1),
            "navigation_status": nav_status,
            "navigation_status_text": NAVIGATION_STATUS.get(nav_status, ""),
            "longitude": decimal_longitude(sint(bits, 44, 18), 600),
            "latitude": decimal_latitude(sint(bits, 62, 17), 600),
            "speed_over_ground": None if sog_raw == 63 else sog_raw,
            "course_over_ground": None if cog_raw == 511 else cog_raw,
            "gnss_position_status": uint(bits, 94, 1),
            "spare": uint(bits, 95, 1),
        }
    )
    return row


DECODERS = {
    1: decode_type_1_2_3,
    2: decode_type_1_2_3,
    3: decode_type_1_2_3,
    5: decode_type_5,
    18: decode_type_18,
    19: decode_type_19,
    24: decode_type_24,
    27: decode_type_27,
}


def assemble_messages(sentences: list[NmeaSentence]) -> tuple[list[NmeaSentence], int]:
    complete: list[NmeaSentence] = []
    partials: dict[tuple[str, str, str, int], dict[int, NmeaSentence]] = {}

    for sentence in sentences:
        if sentence.total_fragments == 1:
            complete.append(sentence)
            continue

        key = (
            sentence.sentence_type,
            sentence.sequence_id,
            sentence.channel,
            sentence.total_fragments,
        )
        partials.setdefault(key, {})[sentence.fragment_number] = sentence
        fragments = partials[key]
        if len(fragments) != sentence.total_fragments:
            continue

        ordered = [fragments[number] for number in range(1, sentence.total_fragments + 1)]
        first = ordered[0]
        last = ordered[-1]
        complete.append(
            NmeaSentence(
                line_number=first.line_number,
                raw_line=first.raw_line,
                sentence_type=first.sentence_type,
                total_fragments=first.total_fragments,
                fragment_number=first.fragment_number,
                sequence_id=first.sequence_id,
                channel=first.channel,
                payload="".join(fragment.payload for fragment in ordered),
                fill_bits=last.fill_bits,
                tag_values=first.tag_values,
                valid_checksum_count=sum(fragment.valid_checksum_count for fragment in ordered),
                invalid_checksum_count=sum(fragment.invalid_checksum_count for fragment in ordered),
            )
        )
        del partials[key]

    incomplete_count = sum(len(fragments) for fragments in partials.values())
    return complete, incomplete_count


def decode_sentence(sentence: NmeaSentence, include_unsupported: bool) -> tuple[dict[str, Any] | None, bool]:
    bits = payload_to_bits(sentence.payload, sentence.fill_bits)
    message_type = uint(bits, 0, 6)
    decoder = DECODERS.get(message_type)
    if decoder is None:
        if not include_unsupported:
            return None, True
        return base_row(sentence, message_type, bits), True
    return decoder(sentence, bits, message_type), False


def message_header_for_error(sentence: NmeaSentence) -> tuple[int | None, int | None]:
    try:
        bits = payload_to_bits(sentence.payload, sentence.fill_bits)
        return uint(bits, 0, 6), uint(bits, 8, 30)
    except Exception:
        return None, None


def read_sentences(path: Path, strict_checksum: bool) -> tuple[list[NmeaSentence], int, int]:
    sentences: list[NmeaSentence] = []
    invalid_lines = 0
    input_lines = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, start=1):
            input_lines += 1
            if not line.strip():
                continue
            try:
                sentence = parse_nmea_line(line, line_number, strict_checksum)
            except Exception:
                invalid_lines += 1
                if strict_checksum:
                    raise
                continue
            if sentence is None:
                invalid_lines += 1
                continue
            sentences.append(sentence)
    return sentences, input_lines, invalid_lines


def print_decode_error(error: DecodeError) -> None:
    message_type = "" if error.message_type is None else f" type={error.message_type}"
    mmsi = "" if error.mmsi is None else f" mmsi={error.mmsi}"
    print(
        f"Skipping undecodable AIS record at source line {error.source_line}{message_type}{mmsi}: "
        f"{error.error}"
    )


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def default_stats_path(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.stem}_stats.csv")


def isoformat_unix_time(value: int | None) -> str:
    if value is None:
        return ""
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat()


def int_or_none(value: Any) -> int | None:
    if value in ("", None):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def aggregate_stats(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[int, str], MessageTypeSourceStats] = {}

    for row in rows:
        message_type_value = int_or_none(row.get("message_type"))
        if message_type_value is None:
            continue

        ais_source = str(row.get("tag_station") or "unknown")
        key = (message_type_value, ais_source)
        if key not in grouped:
            grouped[key] = MessageTypeSourceStats(
                message_type=message_type_value,
                ais_source=ais_source,
                mmsis=set(),
            )

        stats = grouped[key]
        stats.message_count += 1
        if stats.mmsis is not None and row.get("mmsi"):
            stats.mmsis.add(str(row["mmsi"]))

        tag_unix_time = int_or_none(row.get("tag_unix_time"))
        if tag_unix_time is not None:
            stats.min_tag_unix_time = (
                tag_unix_time
                if stats.min_tag_unix_time is None
                else min(stats.min_tag_unix_time, tag_unix_time)
            )
            stats.max_tag_unix_time = (
                tag_unix_time
                if stats.max_tag_unix_time is None
                else max(stats.max_tag_unix_time, tag_unix_time)
            )

        stats.valid_checksum_count += int_or_none(row.get("nmea_valid_checksum_count")) or 0
        stats.invalid_checksum_count += int_or_none(row.get("nmea_invalid_checksum_count")) or 0

    summary_rows: list[dict[str, Any]] = []
    for stats in sorted(grouped.values(), key=lambda item: (item.message_type, item.ais_source)):
        summary_rows.append(
            {
                "message_type": stats.message_type,
                "ais_source": stats.ais_source,
                "message_count": stats.message_count,
                "unique_mmsi_count": len(stats.mmsis or set()),
                "min_tag_unix_time": stats.min_tag_unix_time or "",
                "max_tag_unix_time": stats.max_tag_unix_time or "",
                "min_tag_datetime_utc": isoformat_unix_time(stats.min_tag_unix_time),
                "max_tag_datetime_utc": isoformat_unix_time(stats.max_tag_unix_time),
                "valid_checksum_count": stats.valid_checksum_count,
                "invalid_checksum_count": stats.invalid_checksum_count,
            }
        )
    return summary_rows


def write_stats_csv(rows: list[dict[str, Any]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=STATS_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def print_stats_table(rows: list[dict[str, Any]]) -> None:
    if not rows:
        print("No decoded AIS messages to summarise.")
        return

    headers = [
        ("message_type", "Type"),
        ("ais_source", "AIS Source"),
        ("message_count", "Messages"),
        ("unique_mmsi_count", "Unique MMSI"),
        ("min_tag_datetime_utc", "Min Timestamp UTC"),
        ("max_tag_datetime_utc", "Max Timestamp UTC"),
        ("valid_checksum_count", "Valid Cksum"),
        ("invalid_checksum_count", "Invalid Cksum"),
    ]
    widths = []
    for key, label in headers:
        widths.append(max(len(label), *(len(str(row.get(key, ""))) for row in rows)))

    header_line = "  ".join(label.ljust(width) for (_, label), width in zip(headers, widths))
    rule = "  ".join("-" * width for width in widths)
    print("\nAIS decoded data summary")
    print(header_line)
    print(rule)
    for row in rows:
        print(
            "  ".join(
                str(row.get(key, "")).ljust(width)
                for (key, _), width in zip(headers, widths)
            )
        )


def parse_file(args: argparse.Namespace) -> tuple[Path, Path, ParseStats, list[dict[str, Any]]]:
    input_path = Path(args.input).expanduser()
    if not input_path.exists():
        raise FileNotFoundError(f"input file not found: {input_path}")

    output_path = Path(args.output).expanduser() if args.output else input_path.with_suffix(".csv")
    stats_output_path = (
        Path(args.stats_output).expanduser()
        if args.stats_output
        else default_stats_path(output_path)
    )
    sentences, input_lines, invalid_lines = read_sentences(input_path, args.strict_checksum)
    messages, incomplete_multipart = assemble_messages(sentences)

    rows: list[dict[str, Any]] = []
    skipped_message_types = 0
    decode_errors: list[DecodeError] = []
    for message in messages:
        try:
            row, skipped = decode_sentence(message, args.include_unsupported)
        except Exception as exc:
            message_type, mmsi = message_header_for_error(message)
            error = DecodeError(
                source_line=message.line_number,
                message_type=message_type,
                mmsi=mmsi,
                error=str(exc),
            )
            decode_errors.append(error)
            print_decode_error(error)
            continue
        if skipped:
            skipped_message_types += 1
        if row is not None:
            rows.append(row)

    write_csv(rows, output_path)
    summary_rows = aggregate_stats(rows)
    write_stats_csv(summary_rows, stats_output_path)
    stats = ParseStats(
        input_lines=input_lines,
        nmea_sentences=len(sentences),
        assembled_messages=len(messages),
        decoded_messages=len(rows),
        skipped_message_types=skipped_message_types,
        decode_errors=len(decode_errors),
        invalid_lines=invalid_lines,
        incomplete_multipart=incomplete_multipart,
    )
    return output_path, stats_output_path, stats, summary_rows


def main() -> int:
    args = parse_args()
    output_path, stats_output_path, stats, summary_rows = parse_file(args)
    print(f"Input lines: {stats.input_lines}")
    print(f"NMEA sentences: {stats.nmea_sentences}")
    print(f"Assembled AIS messages: {stats.assembled_messages}")
    print(f"Decoded CSV rows: {stats.decoded_messages}")
    print(f"Skipped unsupported AIS message types: {stats.skipped_message_types}")
    print(f"Skipped undecodable AIS records: {stats.decode_errors}")
    print(f"Invalid/non-AIS lines: {stats.invalid_lines}")
    print(f"Incomplete multipart fragments: {stats.incomplete_multipart}")
    print(f"Output CSV: {output_path}")
    print(f"Stats CSV: {stats_output_path}")
    print_stats_table(summary_rows)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
