[CmdletBinding()]
param(
    [string]$Server,

    [ValidateRange(1, 65535)]
    [int]$Port = 22,

    [string]$User,

    [string]$Password,

    [Alias("ssh-key")]
    [string]$SshKey,

    [Alias("key-passphrase")]
    [string]$KeyPassphrase,

    [Alias("host-key-fingerprint")]
    [string]$HostKeyFingerprint,

    [Alias("remote-path")]
    [string]$RemotePath,

    [Alias("local-path")]
    [string]$LocalPath,

    [Alias("create-dir")]
    [switch]$CreateDir,

    [Alias("transfer-mode")]
    [ValidateSet("BINARY", "ASCII")]
    [string]$TransferMode = "BINARY",

    [Alias("winscp-exe-path")]
    [string]$WinScpExePath = "C:\Program Files (x86)\WinSCP\WinSCP.exe",

    [Alias("winscp-dll-path")]
    [string]$WinScpDllPath = "C:\Program Files (x86)\WinSCP\WinSCPnet.dll",

    [Alias("h")]
    [switch]$Help
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

function Show-Usage
{
    Write-Host @"
LLIDS SFTP upload utility

Usage:
  powershell -File .\llids_sftp_push.ps1 [parameters]

Required parameters:
  -Server <hostname>                 SFTP server hostname.
  -User <username>                   SFTP username.
  -RemotePath <path>                 Remote destination directory.
                                     Alias: -remote-path
  -LocalPath <path-or-pattern>       Local file path or wildcard pattern.
                                     Alias: -local-path
  One of:
  -Password <password>               SFTP account password.
  -SshKey <private-key-file>         Client SSH private-key file.
                                     Alias: -ssh-key

Optional parameters:
  -Port <number>                     SFTP port. Default: 22.
  -KeyPassphrase <passphrase>        Private-key passphrase.
                                     Alias: -key-passphrase
  -HostKeyFingerprint <fingerprint>  Expected SSH server host-key fingerprint.
                                     Alias: -host-key-fingerprint
                                     If omitted, host-key verification is disabled.
  -CreateDir                         Create missing remote directories.
                                     Alias: -create-dir
  -TransferMode <BINARY|ASCII>       Transfer mode. Default: BINARY.
                                     Alias: -transfer-mode
  -WinScpExePath <path>              Path to WinSCP.exe.
                                     Alias: -winscp-exe-path
                                     Default: C:\Program Files (x86)\WinSCP\WinSCP.exe
  -WinScpDllPath <path>              Path to WinSCPnet.dll.
                                     Alias: -winscp-dll-path
                                     Default: C:\Program Files (x86)\WinSCP\WinSCPnet.dll
  -Help                              Show this help and exit.
                                     Alias: -h

Password example:
  powershell -File .\llids_sftp_push.ps1 ``
    -Server "sftp.example.com" ``
    -User "username" ``
    -Password "password" ``
    -LocalPath ".\exports\*.csv" ``
    -RemotePath "/incoming" ``
    -CreateDir ``
    -HostKeyFingerprint "SHA256:..."

Private-key example:
  powershell -File .\llids_sftp_push.ps1 ``
    -Server "sftp.example.com" ``
    -User "username" ``
    -SshKey ".\private-key.sshkey" ``
    -KeyPassphrase "passphrase" ``
    -LocalPath ".\test.txt" ``
    -RemotePath "/incoming"
"@
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

function Resolve-RequiredFilePath
{
    param(
        [string]$Path,
        [string]$Description
    )

    $cleanedPath = Get-CleanPathArgument $Path
    $expandedPath = [Environment]::ExpandEnvironmentVariables($cleanedPath)
    if (!(Test-Path -LiteralPath $expandedPath -PathType Leaf))
    {
        throw "$Description not found: $expandedPath"
    }
    return (Resolve-Path -LiteralPath $expandedPath -ErrorAction Stop).ProviderPath
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

if ($Help)
{
    Show-Usage
    exit 0
}

$currentStage = "validating parameters"
try
{
    $requiredParameters = @{
        "Server" = $Server
        "User" = $User
        "RemotePath" = $RemotePath
        "LocalPath" = $LocalPath
    }
    foreach ($requiredParameter in $requiredParameters.GetEnumerator())
    {
        if ([string]::IsNullOrWhiteSpace([string]$requiredParameter.Value))
        {
            throw "Missing required parameter -$($requiredParameter.Key). Use -Help for usage."
        }
    }

    if ([string]::IsNullOrWhiteSpace($Password) -and [string]::IsNullOrWhiteSpace($SshKey))
    {
        throw "At least one of -Password or -SshKey is required."
    }

    $currentStage = "locating the WinSCP files"
    $resolvedWinScpDllPath = Resolve-RequiredFilePath `
        -Path $WinScpDllPath `
        -Description "WinSCP .NET assembly"
    $resolvedWinScpExePath = Resolve-RequiredFilePath `
        -Path $WinScpExePath `
        -Description "WinSCP executable"
    Write-Host "WinSCP .NET assembly = $resolvedWinScpDllPath"
    Write-Host "WinSCP executable = $resolvedWinScpExePath"

    $currentStage = "loading the WinSCP .NET assembly"
    Add-Type -Path $resolvedWinScpDllPath

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
    $session.ExecutablePath = $resolvedWinScpExePath
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
