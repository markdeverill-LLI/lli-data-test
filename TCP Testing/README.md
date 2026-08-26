# Google Cloud Storage HMAC Probe

Use `gcs_hmac_probe.py` to test Google Cloud Storage credentials supplied as:

- a bucket URL such as `gs://client-bucket/some/path`
- an HMAC access ID
- an HMAC secret

The script uploads a small probe object, downloads it, compares the bytes, and deletes it unless you pass `--keep`.

## Usage

```bash
export GCS_ACCESS_ID="GOOG..."
export GCS_SECRET="..."

python3 gcs_hmac_probe.py "gs://client-bucket/some/path"
```

To upload a specific file as the test payload:

```bash
python3 gcs_hmac_probe.py "gs://client-bucket/some/path" --file ./sample.txt
```

To leave the uploaded object in the bucket:

```bash
python3 gcs_hmac_probe.py "gs://client-bucket/some/path" --keep
```

## Notes

- `gs://client-bucket/some/path` means bucket `client-bucket` and object prefix `some/path`.
- The credentials must be Google Cloud Storage HMAC credentials. Service-account JSON keys use a different auth flow.
- Successful output ends with `Result: credentials can write to and read from this bucket path`.

# AIS Parquet Statistics

Use `ais_parquet_stats.py` to scan an hourly AIS Parquet file or a directory of Parquet files and report:

- total file volume in bytes and MB
- total record count
- list of data fields and their Parquet/Arrow data types
- unique counts and top-value distributions for `IMO` and `mmsi`
- minimum and maximum `messageTimestamp`
- missing expected columns

## Usage

```bash
python3 -m pip install pyarrow
python3 ais_parquet_stats.py ./hourly_ais_delivery.parquet
```

For a directory containing one or more `.parquet` files:

```bash
python3 ais_parquet_stats.py ./hourly_delivery_folder
```

To output JSON for automation:

```bash
python3 ais_parquet_stats.py ./hourly_ais_delivery.parquet --json
```

To scan other columns:

```bash
python3 ais_parquet_stats.py ./hourly_ais_delivery.parquet \
  --distinct-column IMO \
  --distinct-column mmsi \
  --distinct-column VesselName \
  --timestamp-column messageTimestamp
```

# AIS NMEA TCP Capture

Use `ais_nmea_tcp_capture.py` to test the Lloyd's List Intelligence AIS TCP stream and capture NMEA sentences to a timestamped output file.

By default, the script verifies that the public outbound IP is `89.37.64.198`, connects to `subscriber-v2.lloydslistintelligence.com:32100`, captures 100 NMEA records, and writes them to `output/<start timestamp>.nmea`. If no data is received for 120 seconds, the script stops and reports how many records were received.

The public outbound IP check uses `https://api.ipify.org` before the AIS TCP connection is opened. The script also reports the TCP local endpoint used for the stream connection; that address is often a private or interface address and may differ from the public NAT IP seen by LLI.

## Usage

```bash
python3 ais_nmea_tcp_capture.py
```

To capture a different number of records:

```bash
python3 ais_nmea_tcp_capture.py --records 1000
```

To change the output folder or idle timeout:

```bash
python3 ais_nmea_tcp_capture.py --output-dir ./nmea-output --idle-timeout 120
```

To try the alternate LLI AWS NLB service address:

```bash
python3 ais_nmea_tcp_capture.py \
  --host lli-dev-ais-cl-v2-nlb-847474f9aab2532a.elb.eu-west-1.amazonaws.com
```

To connect even when testing from a different public IP:

```bash
python3 ais_nmea_tcp_capture.py --allow-public-ip-mismatch
```

To skip the public IP check entirely:

```bash
python3 ais_nmea_tcp_capture.py --skip-public-ip-check
```

# AIS NMEA Parser

Use `ais_nmea_parse.py` to decode a captured AIS NMEA file to CSV. It handles IEC tag blocks, multipart `!AIVDM` records, and decodes AIS message types `1`, `2`, `3`, `18`, `19`, and `27` for positions plus `5` and `24` for static/voyage records. It also writes a companion statistics CSV and prints the same summary to the command line.

Malformed, truncated, or otherwise undecodable AIS records are skipped rather than stopping the parse. The parser prints the source line number, message type where available, MMSI where available, and the decode error before continuing.

By default, the CSV is written next to the input file with a `.csv` suffix:

```bash
python3 ais_nmea_parse.py output/20260812T134850Z.nmea
```

To choose the output file:

```bash
python3 ais_nmea_parse.py output/20260812T134850Z.nmea \
  --output output/20260812T134850Z_decoded.csv
```

To choose the statistics output file:

```bash
python3 ais_nmea_parse.py output/20260812T134850Z.nmea \
  --output output/20260812T134850Z_decoded.csv \
  --stats-output output/20260812T134850Z_stats.csv
```

To include minimal rows for unsupported AIS message types as well:

```bash
python3 ais_nmea_parse.py output/20260812T134850Z.nmea --include-unsupported
```

# AIS GIS Positions Export

Use `ais_gis_positions.py` to read decoded AIS CSV data and write a minimal GIS plotting CSV from position messages. The output contains `mmsi`, `timestamp`, `latitude`, `longitude`, `cog`, `sog`, `source`, `vessel_name`, `callsign`, `length`, and `vesseltype_code`.

As it streams through the decoded file, the script keeps the latest prior static/voyage data for each MMSI from AIS message types `5` and `24`. Position rows are enriched only when static data has already appeared earlier in the file for that MMSI.

```bash
python3 ais_gis_positions.py output/20260812T142135Z_decoded.csv \
  --output output/20260812T142135Z_gis_positions.csv
```

To also create Kepler.gl-friendly GeoJSON vessel tracks as one LineString per MMSI:

```bash
python3 ais_gis_positions.py output/20260812T142135Z_decoded.csv \
  --output output/20260812T142135Z_gis_positions.csv \
  --geojson-output output/20260812T142135Z_tracks.geojson
```

GeoJSON features include MMSI, first and last timestamp, point count, recognised source list, and the latest known vessel name, callsign, length, and vessel type code. Tracks with fewer than two points are skipped by default; adjust that threshold with `--min-track-points`.
