param(
    [string]$SecretName = "prod/dataservices/sharedsecrets",
    [string]$Region,
    [string]$Profile,
    [string]$Prefix = "llids-",
    [string]$OutputFile
)

$ErrorActionPreference = "Stop"

function Get-AwsSecretJson
{
    param(
        [string]$Name,
        [string]$AwsRegion,
        [string]$AwsProfile
    )

    if ($null -eq (Get-Command "aws" -ErrorAction SilentlyContinue))
    {
        throw "AWS CLI was not found. Install AWS CLI v2 and ensure 'aws' is on PATH."
    }

    $awsArguments = @(
        "secretsmanager",
        "get-secret-value",
        "--secret-id", $Name,
        "--output", "json",
        "--no-cli-pager"
    )
    if (![string]::IsNullOrWhiteSpace($AwsRegion))
    {
        $awsArguments += @("--region", $AwsRegion)
    }
    if (![string]::IsNullOrWhiteSpace($AwsProfile))
    {
        $awsArguments += @("--profile", $AwsProfile)
    }

    $awsOutput = & aws @awsArguments 2>&1
    if ($LASTEXITCODE -ne 0)
    {
        throw "AWS Secrets Manager request failed: $($awsOutput -join [Environment]::NewLine)"
    }

    try
    {
        return ($awsOutput -join [Environment]::NewLine) | ConvertFrom-Json
    }
    catch
    {
        throw "AWS CLI returned invalid JSON: $($_.Exception.Message)"
    }
}

function Get-SecretString
{
    param([object]$AwsResponse)

    if ($null -ne $AwsResponse.SecretString)
    {
        return [string]$AwsResponse.SecretString
    }
    if ($null -ne $AwsResponse.SecretBinary)
    {
        try
        {
            $secretBytes = [Convert]::FromBase64String([string]$AwsResponse.SecretBinary)
            return [Text.Encoding]::UTF8.GetString($secretBytes)
        }
        catch
        {
            throw "AWS SecretBinary could not be decoded as base64 UTF-8 text."
        }
    }
    throw "AWS returned no SecretString or SecretBinary value for '$SecretName'."
}

function ConvertTo-SecretEnvironmentValues
{
    param(
        [string]$SecretValue,
        [string]$RequiredPrefix
    )

    try
    {
        $secretObject = $SecretValue | ConvertFrom-Json
    }
    catch
    {
        throw "The AWS secret value is not valid JSON: $($_.Exception.Message)"
    }

    if ($null -eq $secretObject -or $secretObject -isnot [PSCustomObject])
    {
        throw "The AWS secret must contain a JSON object of key/value pairs."
    }

    $properties = @($secretObject.PSObject.Properties)
    if ($properties.Count -eq 0)
    {
        throw "The AWS secret contains no key/value pairs."
    }

    $environmentValues = @{}
    foreach ($property in $properties)
    {
        $key = [string]$property.Name
        if (
            ![string]::IsNullOrEmpty($RequiredPrefix) -and
            !$key.StartsWith($RequiredPrefix, [StringComparison]::Ordinal)
        )
        {
            throw "Secret key '$key' does not start with required prefix '$RequiredPrefix'."
        }
        if (
            $null -eq $property.Value -or
            $property.Value -is [PSCustomObject] -or
            (
                $property.Value -is [System.Collections.IEnumerable] -and
                $property.Value -isnot [string]
            )
        )
        {
            throw "Secret value for '$key' must be a scalar value."
        }

        $value = [string]$property.Value
        if ($key.Contains([char]0) -or $value.Contains([char]0))
        {
            throw "Secret key '$key' contains a null character."
        }
        $environmentValues[$key] = $value
    }

    return $environmentValues
}

try
{
    $awsResponse = Get-AwsSecretJson `
        -Name $SecretName `
        -AwsRegion $Region `
        -AwsProfile $Profile
    $secretString = Get-SecretString -AwsResponse $awsResponse
    $environmentValues = ConvertTo-SecretEnvironmentValues `
        -SecretValue $secretString `
        -RequiredPrefix $Prefix

    foreach ($entry in $environmentValues.GetEnumerator())
    {
        [Environment]::SetEnvironmentVariable(
            [string]$entry.Key,
            [string]$entry.Value,
            [EnvironmentVariableTarget]::Process
        )
        Write-Host ("Created environment variable: {0}={1}" -f $entry.Key, $entry.Value)
    }

    if (![string]::IsNullOrWhiteSpace($OutputFile))
    {
        $resolvedOutputFile = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutputFile)
        $outputDirectory = [IO.Path]::GetDirectoryName($resolvedOutputFile)
        if (![string]::IsNullOrEmpty($outputDirectory) -and !(Test-Path -LiteralPath $outputDirectory))
        {
            throw "Output directory does not exist: $outputDirectory"
        }

        $jsonOutput = [ordered]@{}
        foreach ($entry in $environmentValues.GetEnumerator())
        {
            $jsonOutput[[string]$entry.Key] = [string]$entry.Value
        }
        $jsonOutput |
            ConvertTo-Json -Depth 2 |
            Set-Content -LiteralPath $resolvedOutputFile -Encoding UTF8
        Write-Warning "Wrote plaintext secrets to $resolvedOutputFile."
    }

    Write-Host (
        "Loaded {0} environment variable(s) from {1} into the current PowerShell process." `
            -f $environmentValues.Count, $SecretName
    )
}
catch
{
    throw "Secret retrieval failed: $($_.Exception.Message)"
}
