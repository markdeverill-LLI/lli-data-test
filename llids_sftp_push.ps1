[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Server,

    [ValidateRange(1, 65535)]
    [int]$Port = 22,

    [Parameter(Mandatory = $true)]
    [string]$User,

    [string]$Password,

    [Alias("ssh-key")]
    [string]$SshKey,

    [Alias("key-passphrase")]
    [string]$KeyPassphrase,

    [Alias("host-key-fingerprint")]
    [string]$HostKeyFingerprint,

    [Parameter(Mandatory = $true)]
    [Alias("remote-path")]
    [string]$RemotePath,

    [Parameter(Mandatory = $true)]
    [Alias("local-path")]
    [string]$LocalPath,

    [Alias("create-dir")]
    [switch]$CreateDir,

    [Alias("transfer-mode")]
    [ValidateSet("BINARY", "ASCII")]
    [string]$TransferMode = "BINARY"
)

$ErrorActionPreference = "Stop"

function Get-CleanPathArgument
{
    param([string]$Path)

    $cleaned = $Path.Trim()
    if ($cleaned.Length -ge 2)
    {
        $firstCharacter = $cleaned.Substring(0, 1)
        $lastCharacter = $cleaned.Substring($cleaned.Length - 1, 1)
        $matchingQuotes = @{
            '"' = '"'
            "'" = "'"
            "“" = "”"
            "‘" = "’"
        }
        if ($matchingQuotes.ContainsKey($firstCharacter) -and $matchingQuotes[$firstCharacter] -eq $lastCharacter)
        {
            $cleaned = $cleaned.Substring(1, $cleaned.Length - 2).Trim()
        }
    }
    return $cleaned
}

function Get-NormalizedRemotePath
{
    param([string]$Path)

    $normalized = (Get-CleanPathArgument $Path) -replace "\\", "/"
    if (!$normalized.StartsWith("/"))
    {
        $normalized = "/" + $normalized
    }
    if (!$normalized.EndsWith("/"))
    {
        $normalized += "/"
    }
    return $normalized
}

function ConvertTo-WinScpHostKeyFingerprint
{
    param([string]$Fingerprint)

    $normalized = $Fingerprint.Trim()
    $normalized = $normalized -replace "(?i)SHA256:", ""
    $normalized = $normalized -replace "(?i)MD5:", ""
    return $normalized
}

function Assert-PrivateKeyFile
{
    param([string]$Path)

    if (!(Test-Path -LiteralPath $Path -PathType Leaf))
    {
        throw "SSH private key file not found: $Path"
    }

    $firstLine = Get-Content -LiteralPath $Path -TotalCount 1 -ErrorAction Stop
    if (
        $firstLine -match "^(ssh-rsa|ssh-ed25519|ecdsa-sha2-\S+)\s" -or
        $firstLine -eq "---- BEGIN SSH2 PUBLIC KEY ----"
    )
    {
        throw "$Path contains a public key. -SshKey requires the matching private key file."
    }
}

function Get-MatchedLocalFiles
{
    param([string]$PathPattern)

    $files = @(
        Resolve-Path -Path $PathPattern -ErrorAction SilentlyContinue |
            ForEach-Object { Get-Item -LiteralPath $_.ProviderPath -ErrorAction Stop } |
            Where-Object { !$_.PSIsContainer }
    )
    if ($files.Count -eq 0)
    {
        throw "No local files matched: $PathPattern"
    }
    return $files
}

function Ensure-RemoteDirectory
{
    param(
        $Session,
        [string]$Path,
        [bool]$Create
    )

    if (!$Create)
    {
        if (!$Session.FileExists($Path))
        {
            throw [System.IO.DirectoryNotFoundException]::new("Directory $Path does not exist")
        }
        Write-Host "Remote directory exists: $Path"
        return
    }

    $currentPath = ""
    foreach ($pathPart in $Path.Trim("/").Split("/"))
    {
        if ([string]::IsNullOrEmpty($pathPart))
        {
            continue
        }
        $currentPath += "/" + $pathPart
        if ($Session.FileExists($currentPath))
        {
            Write-Host "Remote directory exists: $currentPath"
        }
        else
        {
            Write-Host "Creating remote directory: $currentPath"
            $Session.CreateDirectory($currentPath)
        }
    }
}

$currentStage = "validating parameters"
try
{
    if ([string]::IsNullOrWhiteSpace($Password) -and [string]::IsNullOrWhiteSpace($SshKey))
    {
        throw "At least one of -Password or -SshKey is required."
    }

    $currentStage = "locating the WinSCP .NET assembly"
    $winScpAssemblyPath = "C:\Program Files (x86)\WinSCP\WinSCPnet.dll"
    if (!(Test-Path -LiteralPath $winScpAssemblyPath -PathType Leaf))
    {
        throw "WinSCP .NET assembly not found: $winScpAssemblyPath"
    }
    Add-Type -Path $winScpAssemblyPath

    $currentStage = "normalizing local and remote paths"
    $cleanLocalPath = Get-CleanPathArgument $LocalPath
    $normalizedRemotePath = Get-NormalizedRemotePath $RemotePath
    Write-Host "workingDirectory = $((Get-Location).ProviderPath)"
    Write-Host "localPath input = $LocalPath"
    Write-Host "localPath cleaned = $cleanLocalPath"

    $currentStage = "resolving local files"
    $localFiles = @(Get-MatchedLocalFiles $cleanLocalPath)

    $currentStage = "validating the SSH private key"
    $cleanSshKey = $null
    if (![string]::IsNullOrWhiteSpace($SshKey))
    {
        $cleanSshKey = Get-CleanPathArgument $SshKey
        Assert-PrivateKeyFile $cleanSshKey
        $cleanSshKey = (Resolve-Path -LiteralPath $cleanSshKey).Path
    }

    $currentStage = "configuring the SFTP session"
    Write-Host "localPath = $cleanLocalPath"
    Write-Host "remotePath = $normalizedRemotePath"
    Write-Host "create Directory = $($CreateDir.IsPresent)"
    Write-Host "transferMode = $TransferMode"
    Write-Host "files matched = $($localFiles.Count)"
    foreach ($localFile in $localFiles)
    {
        Write-Host ("  {0} ({1} bytes)" -f $localFile.FullName, $localFile.Length)
    }
    Write-Host ("authentication = {0}" -f $(if ($cleanSshKey) { "SSH private key" } else { "username/password" }))
    if ($cleanSshKey)
    {
        Write-Host "private key = $cleanSshKey"
        Write-Host ("private key passphrase = {0}" -f $(if ($KeyPassphrase) { "supplied" } else { "not supplied" }))
    }

    $sessionOptions = New-Object WinSCP.SessionOptions
    $sessionOptions.Protocol = [WinSCP.Protocol]::Sftp
    $sessionOptions.HostName = $Server
    $sessionOptions.PortNumber = $Port
    $sessionOptions.UserName = $User

    if (![string]::IsNullOrWhiteSpace($Password))
    {
        $sessionOptions.Password = $Password
    }
    if ($cleanSshKey)
    {
        $sessionOptions.SshPrivateKeyPath = $cleanSshKey
        if (![string]::IsNullOrWhiteSpace($KeyPassphrase))
        {
            $sessionOptions.PrivateKeyPassphrase = $KeyPassphrase
        }
    }

    if (![string]::IsNullOrWhiteSpace($HostKeyFingerprint))
    {
        $winScpHostKeyFingerprint = ConvertTo-WinScpHostKeyFingerprint $HostKeyFingerprint
        if ($winScpHostKeyFingerprint -ne $HostKeyFingerprint.Trim())
        {
            Write-Host (
                "Normalized host-key fingerprint for legacy WinSCP: {0}" `
                    -f $winScpHostKeyFingerprint
            )
        }
        $sessionOptions.SshHostKeyFingerprint = $winScpHostKeyFingerprint
        Write-Host "host key verification = fingerprint $($sessionOptions.SshHostKeyFingerprint)"
    }
    else
    {
        Write-Warning "No -HostKeyFingerprint supplied; server host-key verification is disabled."
        $sessionOptions.GiveUpSecurityAndAcceptAnySshHostKey = $true
        Write-Host "host key verification = disabled"
    }

    $session = New-Object WinSCP.Session
    try
    {
        $currentStage = "opening the SFTP connection"
        Write-Host "Connecting to ${Server}:$Port as $User..."
        $connectionTimer = [System.Diagnostics.Stopwatch]::StartNew()
        $session.Open($sessionOptions)
        $connectionTimer.Stop()
        Write-Host ("SSH authentication succeeded in {0:N2} seconds." -f $connectionTimer.Elapsed.TotalSeconds)
        Write-Host "SFTP session opened."
        Write-Host "Checking remote directory: $normalizedRemotePath"

        try
        {
            $currentStage = "checking the remote directory"
            Ensure-RemoteDirectory `
                -Session $session `
                -Path $normalizedRemotePath `
                -Create $CreateDir.IsPresent
        }
        catch [System.IO.DirectoryNotFoundException]
        {
            Write-Error "Upload failed: $($_.Exception.Message)"
            exit 5
        }

        $transferOptions = New-Object WinSCP.TransferOptions
        $transferOptions.PreserveTimestamp = $false
        $transferOptions.TransferMode = if ($TransferMode -eq "ASCII")
        {
            [WinSCP.TransferMode]::Ascii
        }
        else
        {
            [WinSCP.TransferMode]::Binary
        }

        $uploadedCount = 0
        foreach ($localFile in $localFiles)
        {
            $currentStage = "uploading $($localFile.FullName)"
            $remoteFile = $normalizedRemotePath + $localFile.Name
            Write-Host "Uploading $($localFile.FullName) to $remoteFile..."
            $transferResult = $session.PutFiles(
                $localFile.FullName,
                $remoteFile,
                $false,
                $transferOptions
            )
            $transferResult.Check()

            $remoteFileInfo = $session.GetFileInfo($remoteFile)
            Write-Host (
                "Upload succeeded: {0} ({1} bytes confirmed on server)" `
                    -f $remoteFile, $remoteFileInfo.Length
            )
            $uploadedCount++
        }
        Write-Host "Completed $uploadedCount upload(s)."
    }
    finally
    {
        $session.Dispose()
    }

    exit 0
}
catch
{
    $caughtError = $_
    $caughtException = $caughtError.Exception
    $exceptionType = $caughtException.GetType().FullName
    $exceptionMessage = $caughtException.Message

    [Console]::Error.WriteLine("Upload failed during stage: " + $currentStage)
    [Console]::Error.WriteLine("Exception: " + $exceptionType + ": " + $exceptionMessage)
    if ($caughtException.InnerException)
    {
        $innerException = $caughtException.InnerException
        [Console]::Error.WriteLine(
            "Inner exception: " +
            $innerException.GetType().FullName +
            ": " +
            $innerException.Message
        )
    }
    if ($caughtError.InvocationInfo -and $caughtError.InvocationInfo.PositionMessage)
    {
        [Console]::Error.WriteLine($caughtError.InvocationInfo.PositionMessage)
    }
    if ($caughtError.ScriptStackTrace)
    {
        [Console]::Error.WriteLine("Script stack trace: " + $caughtError.ScriptStackTrace)
    }
    exit 1
}
