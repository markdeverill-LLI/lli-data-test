param(
    [string]$remotePath,
    [string]$localPath,
    [string]$createDir,
    [string]$transferMode,
    [string]$serverFingerprint,
    [switch]$IgnoreServerFingerprint
)

function Get-SftpEnvironmentVariable
{
    param([string]$Name)

    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($value))
    {
        $value = [Environment]::GetEnvironmentVariable($Name, "Machine")
    }
    if ([string]::IsNullOrWhiteSpace($value))
    {
        throw "$Name is not configured in the current process or Machine environment."
    }
    return $value.Trim().Trim('"')
}

function Get-MaskedPassword
{
    param([string]$Password)

    if ($Password.Length -le 4)
    {
        return "*" * $Password.Length
    }
    return $Password.Substring(0, 2) + ("*" * ($Password.Length - 4)) + $Password.Substring($Password.Length - 2)
}

try
{
    Add-Type -Path "C:\Program Files (x86)\WinSCP\WinSCPnet.dll"

    $sessionOptions = New-Object WinSCP.SessionOptions
    $sessionOptions.Protocol = [WinSCP.Protocol]::Sftp
    $sessionOptions.HostName = Get-SftpEnvironmentVariable "FTP_BVD_SERVER"
    $sessionOptions.UserName = Get-SftpEnvironmentVariable "FTP_BVD_USER"
    $sessionOptions.Password = Get-SftpEnvironmentVariable "FTP_BVD_PWD"

    Write-Host ("FTP_BVD_SERVER = {0}" -f $sessionOptions.HostName)
    Write-Host ("FTP_BVD_USER = {0}" -f $sessionOptions.UserName)
    Write-Host ("FTP_BVD_PWD = {0}" -f (Get-MaskedPassword $sessionOptions.Password))

    $fingerprintVerificationEnabled = $True
    if (![string]::IsNullOrWhiteSpace($serverFingerprint))
    {
        $sshHostKeyFingerprint = $serverFingerprint.Trim().Trim('"')
        $sessionOptions.SshHostKeyFingerprint = $sshHostKeyFingerprint
        Write-Host ("Server fingerprint source = command-line parameter")
        if ($IgnoreServerFingerprint)
        {
            Write-Warning "-serverFingerprint was supplied, so -IgnoreServerFingerprint has been ignored."
        }
    }
    elseif ($IgnoreServerFingerprint)
    {
        Write-Warning "SSH server fingerprint verification is disabled. The connection is vulnerable to man-in-the-middle attacks."
        $sessionOptions.GiveUpSecurityAndAcceptAnySshHostKey = $True
        $fingerprintVerificationEnabled = $False
    }
    else
    {
        $sshHostKeyFingerprint = Get-SftpEnvironmentVariable "FTP_BVD_KEY"
        $sessionOptions.SshHostKeyFingerprint = $sshHostKeyFingerprint
        Write-Host ("Server fingerprint source = FTP_BVD_KEY")
    }

    $session = New-Object WinSCP.Session
    try
    {
        Write-Host "localPath = $localPath"
        Write-Host "remotePath = $remotePath"
        Write-Host "create Directory = $createDir"
        Write-Host "transferMode = $transferMode"
        Write-Host "server fingerprint verification = $fingerprintVerificationEnabled"

        $connectionEndpoint = "{0}:22" -f $sessionOptions.HostName
        $hostKeyPolicy = if ($fingerprintVerificationEnabled) { "verify configured fingerprint" } else { "accept any server host key" }
        Write-Host ("[{0}] Connection attempt starting" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
        Write-Host ("  Protocol: SFTP")
        Write-Host ("  Endpoint: {0}" -f $connectionEndpoint)
        Write-Host ("  Username: {0}" -f $sessionOptions.UserName)
        Write-Host ("  Authentication: username/password")
        Write-Host ("  Host key policy: {0}" -f $hostKeyPolicy)

        $connectionTimer = [System.Diagnostics.Stopwatch]::StartNew()
        try
        {
            $session.Open($sessionOptions)
            $connectionTimer.Stop()
            Write-Host ("[{0}] Connection succeeded in {1:N2} seconds" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $connectionTimer.Elapsed.TotalSeconds)
        }
        catch
        {
            $connectionTimer.Stop()
            Write-Host ("[{0}] Connection failed after {1:N2} seconds" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $connectionTimer.Elapsed.TotalSeconds)
            Write-Host ("  Endpoint: {0}" -f $connectionEndpoint)
            Write-Host ("  Exception type: {0}" -f $_.Exception.GetType().FullName)
            Write-Host ("  Error: {0}" -f $_.Exception.Message)
            throw
        }

        $remotePath = $remotePath -replace "\\", "/"
        if (!$remotePath.EndsWith("/"))
        {
            $remotePath += "/"
        }
        if (!$remotePath.StartsWith("/"))
        {
            $remotePath = "/" + $remotePath
        }

        $mkdir = $False
        if (![string]::IsNullOrEmpty($createDir))
        {
            $mkdir = $createDir.ToUpper() -eq "TRUE"
        }

        if (!$session.FileExists($remotePath))
        {
            if ($mkdir)
            {
                $session.CreateDirectory($remotePath)
            }
            else
            {
                Write-Host ("Directory {0} does not exist" -f $remotePath)
                exit 5
            }
        }

        $transfer = [WinSCP.TransferMode]::Binary
        if (![string]::IsNullOrEmpty($transferMode) -and $transferMode.ToUpper() -eq "ASCII")
        {
            $transfer = [WinSCP.TransferMode]::Ascii
        }

        $transferOptions = New-Object WinSCP.TransferOptions
        $transferOptions.PreserveTimestamp = $False
        $transferOptions.TransferMode = $transfer

        $transferResult = $session.PutFiles($localPath, $remotePath, $False, $transferOptions)
        $transferResult.Check()

        foreach ($uploadedFile in $transferResult.Transfers)
        {
            Write-Host ("Upload of {0} succeeded" -f $uploadedFile.FileName)
        }
    }
    finally
    {
        $session.Dispose()
    }

    exit 0
}
catch [Exception]
{
    Write-Host $_.Exception.Message
    exit 1
}
