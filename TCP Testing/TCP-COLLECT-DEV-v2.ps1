$hostName = "lli-dev-ais-cl-v2-nlb-847474f9aab2532a.elb.eu-west-1.amazonaws.com"
$port = 32100
$maxRecords = 100000
$outputFile = "C:\Users\MarkDeverill\OneDrive - Maritime Insights & Intelligence Limited\Documents\TCP\output\ais_capture_nmea_dev.txt"
$outputFile = "output\ais_capture_nmea_dev2.txt"

$outputFolder = Join-Path $PSScriptRoot "output"

if (-not (Test-Path $outputFolder)) {
    New-Item -ItemType Directory -Path $outputFolder | Out-Null
}

$outputFile = Join-Path $outputFolder "ais_capture_nmea_dev2.txt"

$progressInterval = 10000

$client = $null
$reader = $null
$writer = $null

try {
    Write-Host "Connecting to ${hostName}:${port}..."
    Write-Host "Target records: $maxRecords"
    Write-Host "Output file: $outputFile"

    $client = New-Object System.Net.Sockets.TcpClient
    $client.Connect($hostName, $port)

    Write-Host "TCP CONNECTED"
    Write-Host "Local endpoint:  $($client.Client.LocalEndPoint)"
    Write-Host "Remote endpoint: $($client.Client.RemoteEndPoint)"

    $stream = $client.GetStream()
    $reader = New-Object System.IO.StreamReader($stream)
    $writer = New-Object System.IO.StreamWriter($outputFile, $false)

    $count = 0
    $startTime = Get-Date

    while ($count -lt $maxRecords) {
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
    Write-Host "Saved to: $outputFile"
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
