$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$scriptDir = Join-Path $env:WINDIR 'Setup\Scripts'
New-Item -ItemType Directory -Path $scriptDir -Force | Out-Null
$log = Join-Path $env:WINDIR 'Setup\Scripts\provision-postinstall.log'
$result = Join-Path $env:WINDIR 'Setup\Scripts\provision-postinstall.json'
Start-Transcript -Path $log -Append | Out-Null
try {
    $cfg = ConvertFrom-Json -InputObject @'
{"mac": "020000000008", "address": "192.0.2.102", "prefix": 24, "gateway": "192.0.2.254", "dns": ["4.2.2.4"], "backup_password": "OtherSafePassword123!", "rdp_port": 33096}
'@
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
        throw "Post-install verification failed: IP=$ipOk gateway=$gatewayOk DNS=$dnsOk RDP=$rdpOk listener=$listenerOk"
    }
    @{ success=$true; adapter=$nic.Name; adapter_mac=$nic.MacAddress; address=$cfg.address; rdp_port=$cfg.rdp_port;
       backup_user='iranserver'; timestamp=(Get-Date).ToString('o') } |
        ConvertTo-Json | Set-Content -Path $result -Encoding UTF8
} catch {
    @{ success=$false; error=$_.Exception.Message; timestamp=(Get-Date).ToString('o') } |
        ConvertTo-Json | Set-Content -Path $result -Encoding UTF8
    Write-Error $_
    exit 1
} finally {
    Stop-Transcript | Out-Null
}
