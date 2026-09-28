param([string]$remotePath,[string]$localPath, [String]$createDir,[String]$transferMode)
try{
Add-Type -Path "C:\Program Files (x86)\WinSCP\WinSCPnet.dll"
 $sessionOptions = New-Object WinSCP.SessionOptions
 $sessionOptions.Protocol = [WinSCP.Protocol]::Sftp
   # $sessionOptions.HostName = $Args[0]
   # $sessionOptions.UserName = $Args[1]
   #  $sessionOptions.Password = $Args[2]
   #  $sessionOptions.SshHostKeyFingerprint = "ssh-rsa 2048 " +$Args[3]
    $sessionOptions.HostName = [Environment]::GetEnvironmentVariable("FTP_KITEWORKS_SERVER","Machine")
    $sessionOptions.UserName = [Environment]::GetEnvironmentVariable("FTP_KITEWORKS_USER","Machine")
    $sessionOptions.Password = [Environment]::GetEnvironmentVariable("FTP_KITEWORKS_PWD","Machine")
    $sshHostKeyFingerprint = [Environment]::GetEnvironmentVariable("FTP_KITEWORKS_KEY","Machine")
    if ([string]::IsNullOrWhiteSpace($sshHostKeyFingerprint)) {
        $sessionOptions.GiveUpSecurityAndAcceptAnySshHostKey = $True
    }
    else {
        $sessionOptions.SshHostKeyFingerprint = $sshHostKeyFingerprint
    }
    $session = New-Object WinSCP.Session
     try
    {
         Write-Host ("localPath = $localPath`r`n")
         Write-Host ("remotePath = $remotePath`r`n")
         Write-Host ("create Directory = $createDir`r`n")
         Write-Host ("transferMode = $transferMode`r`n")

        # Connect
        $session.Open($sessionOptions)

        # replace any backslahes with foreward slashes
        $remotePath = $remotePath -replace "\\", "//"

        # Check that remotePath begins and ends with a forward slash
        if (!$remotePath.ToString().EndsWith("/") ){
           $remotePath=$remotePath+"/"
        }
         if (!$remotePath.ToString().StartsWith("/") ){
            $remotePath="/"+$remotePath
        }

        # Check for additional parameter to mkdir if not exist
        $mkdir = $False
         if (![string]::IsNullOrEmpty($createDir) ){
          if ($createDir.ToString().ToUpper() -eq "TRUE"){
            $mkdir = $True
          }
        }

        if (!$session.FileExists($remotePath))
        {
           if ($mkdir){
           $session.CreateDirectory($remotePath)
           }
            else{
              Write-Host ("File {0} does not exist" -f $remotePath)
              exit 5
            }
        }
        $transfer = [WinSCP.TransferMode]::Binary
        if (![string]::IsNullOrEmpty($transferMode) ){
          if ($transferMode.ToUpper() -eq "ASCII"){
           $transfer = [WinSCP.TransferMode]::Ascii
           }
        }


        # Upload files
        $transferOptions = New-Object WinSCP.TransferOptions
        $transferOptions.PreserveTimestamp = $False
        $transferOptions.TransferMode = $transfer

        $transferResult = $session.PutFiles($localPath, $remotePath, $False, $transferOptions)

        #echo $transferResult
        # Throw on any error
        $transferResult.Check()

        # Print results
        foreach ($transfer in $transferResult.Transfers)
        {
            Write-Host ("Upload of {0} succeeded" -f $transfer.FileName)
        }
    }
    finally
    {
        # Disconnect, clean up
        $session.Dispose()
    }

    exit 0
}
catch [Exception]
{
    Write-Host $_.Exception.Message
    exit 1
}
