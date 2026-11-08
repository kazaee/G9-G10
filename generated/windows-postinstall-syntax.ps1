$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$scriptDir = Join-Path $env:WINDIR 'Setup\Scripts'
New-Item -ItemType Directory -Path $scriptDir -Force | Out-Null
$log = Join-Path $env:WINDIR 'Setup\Scripts\provision-postinstall.log'
$result = Join-Path $env:WINDIR 'Setup\Scripts\provision-postinstall.json'
$retryTask = 'IranServer-Provision-PostInstall'
Start-Transcript -Path $log -Append | Out-Null
try {
    $cfg = ConvertFrom-Json -InputObject @'
{"mac": "8CDCD4B7E308", "address": "193.141.127.102", "prefix": 24, "gateway": "193.141.127.254", "dns": ["4.2.2.4"], "backup_password": "TemporaryStrongPassword123!", "rdp_port": 33096, "data_disk_size": 4000753475584, "data_drive": "E", "report_url": null}
'@
    if (Test-Path $result) {
        $prior = Get-Content $result -Raw | ConvertFrom-Json
        if ($prior.success -eq $true) {
            $reported = $true
            if ($cfg.report_url) {
                try {
                    Invoke-WebRequest -Uri $cfg.report_url -Method POST -ContentType 'application/json' `
                        -Body ($prior | ConvertTo-Json -Compress) -TimeoutSec 15 -UseBasicParsing | Out-Null
                } catch {
                    $reported = $false
                    Write-Warning "Install report retry failed: $($_.Exception.Message)"
                }
            }
            if ($reported) {
                try {
                    Unregister-ScheduledTask -TaskName $retryTask -Confirm:$false -ErrorAction SilentlyContinue
                } catch { Write-Warning "Could not remove retry task: $($_.Exception.Message)" }
            }
            return
        }
    }
    $adapters = @(Get-NetAdapter | Where-Object {
        ($_.MacAddress -replace '[:-]', '').ToUpperInvariant() -eq $cfg.mac
    })
    if ($adapters.Count -ne 1) { throw "Expected one network adapter with MAC $($cfg.mac); found $($adapters.Count)" }
    $nic = $adapters[0]
    if ($nic.Status -ne 'Up') {
        $linked = @(Get-NetAdapter | Where-Object { $_.Status -eq 'Up' })
        if ($linked.Count -eq 1) {
            Write-Output "Selected MAC is disconnected; using sole linked adapter $($linked[0].Name)"
            $nic = $linked[0]
        } elseif ($linked.Count -gt 1) {
            throw "Selected MAC is disconnected and $($linked.Count) adapters are linked; cannot choose safely"
        }
    }
    # The specialize answer may have assigned the static address to the
    # selected but disconnected NIC. Move it before configuring the live NIC.
    $stale = @(Get-NetIPAddress -AddressFamily IPv4 -IPAddress $cfg.address -ErrorAction SilentlyContinue |
        Where-Object { $_.InterfaceIndex -ne $nic.ifIndex })
    foreach ($old in $stale) {
        Write-Output "Removing duplicate address from inactive interface $($old.InterfaceIndex)"
        Remove-NetIPAddress -InputObject $old -Confirm:$false
    }
    $existing = @(Get-NetIPAddress -InterfaceIndex $nic.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
        Where-Object { $_.IPAddress -eq $cfg.address -and $_.PrefixLength -eq $cfg.prefix })
    if ($existing.Count -eq 0) {
        Set-NetIPInterface -InterfaceIndex $nic.ifIndex -AddressFamily IPv4 -Dhcp Disabled
        Get-NetIPAddress -InterfaceIndex $nic.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.PrefixOrigin -ne 'WellKnown' } |
            Remove-NetIPAddress -Confirm:$false -ErrorAction SilentlyContinue
        Get-NetRoute -InterfaceIndex $nic.ifIndex -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue |
            Remove-NetRoute -Confirm:$false -ErrorAction SilentlyContinue
        New-NetIPAddress -InterfaceIndex $nic.ifIndex -IPAddress $cfg.address `
            -PrefixLength $cfg.prefix -DefaultGateway $cfg.gateway | Out-Null
    }
    Set-DnsClientServerAddress -InterfaceIndex $nic.ifIndex -ServerAddresses @($cfg.dns)
    Set-TimeZone -Id 'Iran Standard Time'

    $terminal = 'HKLM:\SYSTEM\CurrentControlSet\Control\Terminal Server'
    $listener = Join-Path $terminal 'WinStations\RDP-Tcp'
    $rdpRule = "IranServer-Provision-RDP-TCP-$($cfg.rdp_port)"
    if (-not (Get-NetFirewallRule -Name $rdpRule -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -Name $rdpRule -DisplayName $rdpRule -Direction Inbound `
            -Action Allow -Protocol TCP -LocalPort $cfg.rdp_port -Profile Any | Out-Null
    }
    Set-ItemProperty -Path $terminal -Name fDenyTSConnections -Value 0
    Set-ItemProperty -Path $listener -Name UserAuthentication -Value 1
    Set-ItemProperty -Path $listener -Name PortNumber -Value $cfg.rdp_port
    foreach ($name in 'RemoteDesktop-UserMode-In-TCP','RemoteDesktop-UserMode-In-UDP') {
        Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue | Disable-NetFirewallRule
    }
    if ((Get-Service TermService).Status -eq 'Running') {
        Restart-Service TermService -Force
    } else {
        Start-Service TermService
    }
    if (-not (Get-NetFirewallRule -Name 'IranServer-Provision-ICMPv4' -ErrorAction SilentlyContinue)) {
        New-NetFirewallRule -Name 'IranServer-Provision-ICMPv4' -DisplayName 'IranServer-Provision-ICMPv4' `
            -Direction Inbound -Action Allow -Protocol ICMPv4 -IcmpType 8 -Profile Any | Out-Null
    }
    foreach ($direction in 'Inbound','Outbound') {
        foreach ($protocol in 'TCP','UDP') {
            $name = "IranServer-Provision-NetBIOS-$direction-$protocol"
            if (-not (Get-NetFirewallRule -Name $name -ErrorAction SilentlyContinue)) {
                $rule = @{ Name=$name; DisplayName=$name; Direction=$direction; Action='Block';
                           Protocol=$protocol; Profile='Any' }
                if ($direction -eq 'Inbound') { $rule.LocalPort = @('137','138','139','445') }
                else { $rule.RemotePort = @('137','138','139','445') }
                New-NetFirewallRule @rule | Out-Null
            }
        }
    }
    $account = Get-LocalUser -Name 'iranserver' -ErrorAction SilentlyContinue
    if (-not $account) {
        $password = ConvertTo-SecureString $cfg.backup_password -AsPlainText -Force
        New-LocalUser -Name 'iranserver' -Password $password `
            -Description 'IranServer recovery administrator' | Out-Null
    }
    $administrators = Get-LocalGroup -SID 'S-1-5-32-544'
    $members = @(Get-LocalGroupMember -Group $administrators.Name | Where-Object { $_.Name -like '*\iranserver' })
    if ($members.Count -eq 0) { Add-LocalGroupMember -Group $administrators.Name -Member 'iranserver' }
    if ($null -ne $cfg.data_disk_size) {
        $availableLetters = @(([int][char]$cfg.data_drive)..([int][char]'Z') |
            ForEach-Object { [string][char]$_ } |
            Where-Object { -not (Get-Volume -DriveLetter $_ -ErrorAction SilentlyContinue) })
        if ($availableLetters.Count -eq 0) {
            throw 'No free data drive letter from E through Z; disk not erased'
        }
        $dataDrive = $availableLetters[0]
        $dataCandidates = @(Get-Disk | Where-Object {
            [Math]::Abs([int64]$_.Size - [int64]$cfg.data_disk_size) -le 64MB
        })
        if ($dataCandidates.Count -ne 1) {
            throw "Expected one data disk of the reviewed size; found $($dataCandidates.Count); none erased"
        }
        $dataDisk = $dataCandidates[0]
        $systemDisk = (Get-Partition -DriveLetter C).DiskNumber
        if ($dataDisk.Number -eq $systemDisk -or $dataDisk.IsBoot -or $dataDisk.IsSystem -or
            $dataDisk.OperationalStatus -ne 'Online' -or $dataDisk.IsReadOnly) {
            throw 'Data disk identity/safety check failed; none erased'
        }
        $existingData = @(Get-Partition -DiskNumber $dataDisk.Number -ErrorAction SilentlyContinue |
            Get-Volume -ErrorAction SilentlyContinue |
            Where-Object { $_.FileSystem -eq 'NTFS' -and $_.FileSystemLabel -eq 'Data' -and $_.Size -gt 40GB })
        if ($existingData.Count -eq 1) {
            $dataDrive = [string]$existingData[0].DriveLetter
            if (-not $dataDrive) { throw 'Existing Data volume has no drive letter; manual review required' }
            Write-Output "Keeping existing Data volume at $($dataDrive):"
        } else {
            Write-Output "Erasing reviewed data disk $($dataDisk.Number) and creating $($dataDrive):"
            if ($dataDisk.PartitionStyle -ne 'RAW') {
                Clear-Disk -Number $dataDisk.Number -RemoveData -Confirm:$false
            }
            Initialize-Disk -Number $dataDisk.Number -PartitionStyle GPT
            New-Partition -DiskNumber $dataDisk.Number -UseMaximumSize `
                -DriveLetter $dataDrive | Out-Null
            Format-Volume -DriveLetter $dataDrive -FileSystem NTFS `
                -NewFileSystemLabel Data -Confirm:$false -Force | Out-Null
        }
        $dataVolume = Get-Volume -DriveLetter $dataDrive
        if ($dataVolume.FileSystem -ne 'NTFS' -or $dataVolume.Size -lt 40GB) {
            throw 'Data disk formatting verification failed'
        }
    }
    $ipOk = @(Get-NetIPAddress -InterfaceIndex $nic.ifIndex -AddressFamily IPv4 |
        Where-Object { $_.IPAddress -eq $cfg.address -and $_.PrefixLength -eq $cfg.prefix }).Count -eq 1
    $gatewayOk = @(Get-NetRoute -InterfaceIndex $nic.ifIndex -DestinationPrefix '0.0.0.0/0' |
        Where-Object { $_.NextHop -eq $cfg.gateway }).Count -ge 1
    $configuredDns = @(Get-DnsClientServerAddress -InterfaceIndex $nic.ifIndex -AddressFamily IPv4).ServerAddresses
    $dnsOk = @($cfg.dns | Where-Object { $_ -notin $configuredDns }).Count -eq 0
    $rdpOk = (Get-ItemProperty -Path $terminal).fDenyTSConnections -eq 0 -and
        (Get-ItemProperty -Path $listener).PortNumber -eq $cfg.rdp_port
    $listenerOk = $false
    for ($attempt = 0; $attempt -lt 10; $attempt++) {
        $listenerOk = @(Get-NetTCPConnection -State Listen -LocalPort $cfg.rdp_port -ErrorAction SilentlyContinue).Count -gt 0
        if ($listenerOk) { break }
        Start-Sleep -Seconds 3
    }
    if (-not ($ipOk -and $gatewayOk -and $dnsOk -and $rdpOk -and $listenerOk)) {
        if ($ipOk -and $gatewayOk -and $dnsOk -and $rdpOk -and -not $listenerOk) {
            try {
                if (-not (Get-ScheduledTask -TaskName $retryTask -ErrorAction SilentlyContinue)) {
                    $taskAction = New-ScheduledTaskAction `
                        -Execute (Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe') `
                        -Argument ('-NoProfile -ExecutionPolicy Bypass -File "' +
                                   (Join-Path $scriptDir 'Provision-PostInstall.ps1') + '"')
                    $taskTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) `
                        -RepetitionInterval (New-TimeSpan -Minutes 2) `
                        -RepetitionDuration (New-TimeSpan -Hours 6)
                    $taskSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew
                    Register-ScheduledTask -TaskName $retryTask -Action $taskAction `
                        -Trigger $taskTrigger -Settings $taskSettings `
                        -User 'NT AUTHORITY\SYSTEM' -RunLevel Highest -Force | Out-Null
                }
                Write-Output 'RDP listener is not ready; SYSTEM retry is scheduled without logon'
            } catch {
                Write-Warning "Could not schedule unattended RDP retry: $($_.Exception.Message)"
            }
        }
        throw "Post-install verification failed: IP=$ipOk gateway=$gatewayOk DNS=$dnsOk RDP=$rdpOk listener=$listenerOk"
    }
    $report = @{ event='WINDOWS_POSTINSTALL'; success=$true; adapter=$nic.Name;
       adapter_mac=$nic.MacAddress; address=$cfg.address; rdp_port=$cfg.rdp_port;
       backup_user='iranserver'; data_drive=$(if ($null -ne $cfg.data_disk_size) { $dataDrive } else { $null });
       timestamp=(Get-Date).ToString('o') }
    $report | ConvertTo-Json | Set-Content -Path $result -Encoding UTF8
    $reported = $true
    if ($cfg.report_url) {
        try {
            Invoke-WebRequest -Uri $cfg.report_url -Method POST -ContentType 'application/json' `
                -Body ($report | ConvertTo-Json -Compress) -TimeoutSec 15 -UseBasicParsing | Out-Null
        } catch {
            $reported = $false
            Write-Warning "Install report could not reach the panel: $($_.Exception.Message)"
        }
    }
    if ($reported) {
        try {
            Unregister-ScheduledTask -TaskName $retryTask -Confirm:$false -ErrorAction SilentlyContinue
        } catch { Write-Warning "Could not remove retry task: $($_.Exception.Message)" }
    }
} catch {
    $report = @{ event='WINDOWS_POSTINSTALL'; success=$false;
        error=$_.Exception.Message; timestamp=(Get-Date).ToString('o') }
    $report | ConvertTo-Json | Set-Content -Path $result -Encoding UTF8
    if ($cfg.report_url) {
        try {
            Invoke-WebRequest -Uri $cfg.report_url -Method POST -ContentType 'application/json' `
                -Body ($report | ConvertTo-Json -Compress) -TimeoutSec 15 -UseBasicParsing | Out-Null
        } catch { Write-Warning "Failure report could not reach the panel: $($_.Exception.Message)" }
    }
    Write-Error $_
    exit 1
} finally {
    Stop-Transcript | Out-Null
}
