param(
    [Parameter(Mandatory = $false)]
    [ValidateRange(1, [int]::MaxValue)]
    [int]$MaxRecords = 100000,

    [Parameter(Mandatory = $false)]
    [ValidateNotNullOrEmpty()]
    [string]$OutputFile = "output\ais_capture_nmea_dev2.txt",

    [Parameter(Mandatory = $false)]
    [ValidateNotNullOrEmpty()]
    [string]$ServerAddress = "lli-dev-ais-cl-v2-nlb-847474f9aab2532a.elb.eu-west-1.amazonaws.com"
)

$port = 32100

# Resolve relative output paths from the folder containing this script.
# Full paths (for example C:\captures\output.txt) are used unchanged.
if (-not [System.IO.Path]::IsPathRooted($OutputFile)) {
    $OutputFile = Join-Path $PSScriptRoot $OutputFile
}

$OutputFile = [System.IO.Path]::GetFullPath($OutputFile)
$outputFolder = Split-Path -Parent $OutputFile

if (-not (Test-Path $outputFolder)) {
    New-Item -ItemType Directory -Path $outputFolder -Force | Out-Null
}

$progressInterval = 10000

$client = $null
$reader = $null
$writer = $null

try {
    Write-Host "Connecting to ${ServerAddress}:${port}..."
    Write-Host "Target records: $MaxRecords"
    Write-Host "Output file: $OutputFile"

    $client = New-Object System.Net.Sockets.TcpClient
    $client.Connect($ServerAddress, $port)

    Write-Host "TCP CONNECTED"
    Write-Host "Local endpoint:  $($client.Client.LocalEndPoint)"
    Write-Host "Remote endpoint: $($client.Client.RemoteEndPoint)"

    $stream = $client.GetStream()
    $reader = New-Object System.IO.StreamReader($stream)
    $writer = New-Object System.IO.StreamWriter($OutputFile, $false)

    $count = 0
    $startTime = Get-Date

    while ($count -lt $MaxRecords) {
        $line = $reader.ReadLine()

        if ($null -eq $line) {
            Write-Host ""
            Write-Host "REMOTE STREAM CLOSED after $count records"
            break
        }

        $writer.WriteLine($line)
        $count++

        if (($count % $progressInterval) -eq 0) {
            $writer.Flush()

            $elapsed = (Get-Date) - $startTime
            $rate = if ($elapsed.TotalSeconds -gt 0) {
                [math]::Round($count / $elapsed.TotalSeconds, 1)
            } else {
                0
            }

            Write-Host "$count records captured - $rate records/sec"
        }
    }

    $writer.Flush()

    $elapsed = (Get-Date) - $startTime

    Write-Host ""
    Write-Host "Capture complete"
    Write-Host "Records captured: $count"
    Write-Host "Elapsed time: $($elapsed.ToString())"
    Write-Host "Saved to: $OutputFile"
}
catch {
    Write-Host ""
    Write-Host "ERROR: $($_.Exception.Message)"
}
finally {
    if ($writer) { $writer.Dispose() }
    if ($reader) { $reader.Dispose() }
    if ($client) { $client.Dispose() }
}
