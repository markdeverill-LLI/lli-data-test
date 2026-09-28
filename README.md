# lli-data-test

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