# lli-data-test

## Running Oracle SQL scripts without sqlplus

`oracle_sql_runner.py` replaces the Oracle `sqlplus` command-line tool on the
SCCM server. It runs a `.sql` script containing DDL/DML (including
`CREATE GLOBAL TEMPORARY TABLE` style temporary data sets), PL/SQL blocks, and
`SELECT` statements, and spools `SELECT` output to CSV with headers. It uses
`python-oracledb` in pure-Python "thin" mode, so no Oracle Client / Instant
Client install is required for a simple host:port/service connection. Install
the dependency with `pip install -r requirements.txt` (or `pip install
oracledb`).

```powershell
python oracle_sql_runner.py `
  --sqlfile extract.sql `
  --outputfile extract.csv `
  --logfile extract.log `
  --server dbhost.example.com --port 1521 --service ORCLPDB1 `
  --oracleuser etl_user --password-env ORACLE_PASSWORD
```

Key options:

- `--sqlfile` — the SQL script to run.
- `--outputfile` — CSV file for `SELECT` output when the script has no inline
  `SPOOL` command, or the target to return to after `SPOOL OFF`. Scripts that
  include their own `SPOOL <file>` / `SPOOL OFF` commands are honoured too, so
  existing scripts work unchanged.
- `--logfile` — run log (statement-by-statement progress, row counts, and
  errors); always also logged to stdout.
- `--service` plus `--server`/`--port` (default port `1521`) to connect via
  Easy Connect, or `--dsn` to supply a full connect string/override.
- `--oracleuser` and `--password` (or `--password-env NAME`, default
  `ORACLE_PASSWORD`, to avoid putting the password on the command line; falls
  back to an interactive prompt if neither is supplied).
- `--var NAME=VALUE` / `--arg VALUE` to substitute `&NAME` / `&1`, `&2` ...
  references used by scripts, and `--fail-fast` to stop on the first error
  regardless of the script's `WHENEVER SQLERROR` setting.

Supported sqlplus directives in the script itself: `SET HEADING|FEEDBACK|COLSEP`,
`SPOOL <file>` / `SPOOL OFF`, `WHENEVER SQLERROR EXIT|CONTINUE`, `PROMPT`,
`REM`/`--` comments, `/* */` block comments, and `EXIT`/`QUIT`. Statements
terminated by `;` or a lone `/` (including multi-line PL/SQL blocks) are both
supported.

## Uploading a data extract to Amazon S3

`llids_awss3_upload.py` uploads every file with the requested extension from an
output directory. The AWS CLI must be installed, configured, and available on
`PATH`.

On Windows, do not put a single trailing backslash immediately before the
closing quote around `--path`. It escapes the quote during command-line parsing,
which causes the following options to be consumed as part of the path.

```powershell
python llids_awss3_upload.py `
  --path "Y:\IBI_SCORCH\EXTERNAL\CLIENT\JOTUN\BESPOKE\LLIDS3218_SANCTIONS_SAMPLE\OUTPUT\VESSELS" `
  --bucket "lli-prd-bobsled-bucket-eu-west-1" `
  --prefix "VESSELS" `
  --ext ".CSV"
```

The path may alternatively end with two backslashes:

```text
--path "Y:\path\to\VESSELS\\"
```

### Uploading files from subfolders

Pass `--copysubfolders Y` to recursively upload every file matching `--ext`.
The default is `N`, which uploads all matching files directly inside the
specified folder without searching its subfolders.

Omit `--prefix` to upload the folders directly beneath the S3 bucket root:

```powershell
python llids_awss3_upload.py `
  --path "Y:\IBI_SCORCH\EXTERNAL\CLIENT\JOTUN\BESPOKE\LLIDS3218_SANCTIONS_SAMPLE\OUTPUT" `
  --bucket "lli-prd-bobsled-bucket-eu-west-1" `
  --ext ".CSV" `
  --copysubfolders Y
```

Subfolder paths are preserved beneath the bucket root. For example,
`OUTPUT\VESSELS\vessels.csv` is uploaded to
`s3://lli-prd-bobsled-bucket-eu-west-1/VESSELS/vessels.csv`.

When `--prefix "JOTUN"` is supplied, the same file is uploaded to
`s3://lli-prd-bobsled-bucket-eu-west-1/JOTUN/VESSELS/vessels.csv`.

`--prefix` sets the destination folder in S3; it does not filter source
filenames. For example, all CSV files directly inside `PROGRAM` can be uploaded
under `BobSled_PFA` with:

```powershell
python llids_awss3_upload.py `
  --path "Z:\IBI_SCORCH\INTERNAL\DATA_LOADING\SHADOWFLEETLISTS\PROGRAM" `
  --bucket "lli-prd-snowflake-exports-eu-west-1" `
  --ext ".csv" `
  --prefix "BobSled_PFA"
```

## Listing files in Amazon S3

`llids_aws23_list.py` lists a folder in an S3 bucket. By default, only the
specified folder is listed:

```powershell
python llids_aws23_list.py `
  --bucket "lli-prd-snowflake-exports-eu-west-1" `
  --path "BobSled_PFA" `
  --recursive N
```

Use `--recursive Y` to include files in every subfolder:

```powershell
python llids_aws23_list.py `
  --bucket "lli-prd-snowflake-exports-eu-west-1" `
  --path "BobSled_PFA" `
  --recursive Y
```